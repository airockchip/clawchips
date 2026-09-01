#include "sampling_bridge.h"

#include <xgrammar/xgrammar.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstring>
#include <limits>
#include <memory>
#include <mutex>
#include <new>
#include <optional>
#include <random>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <utility>
#include <variant>
#include <vector>

#if defined(__aarch64__)
#include <arm_neon.h>
#endif

namespace {

using clock_type = std::chrono::steady_clock;
constexpr size_t top_k_block_size = 128;
constexpr size_t sparse_mask_candidate_limit = 4096;
constexpr char tool_call_open[] = "<tool_call>";
constexpr size_t tool_call_open_size = sizeof(tool_call_open) - 1;

struct candidate {
    int32_t id;
    float logit;
    float p;
};

struct repeat_entry {
    int32_t token_id;
    uint32_t count;
};

constexpr size_t direct_global_winner_limit = 4;
constexpr size_t sparse_mask_block_candidate_limit = 16;

thread_local std::string last_error;

double elapsed_ms(clock_type::time_point start) {
    return std::chrono::duration<double, std::milli>(clock_type::now() - start).count();
}

float fp16_to_fp32(uint16_t value) {
    const uint32_t sign = static_cast<uint32_t>(value & 0x8000u) << 16;
    const uint32_t exponent = (value >> 10) & 0x1fu;
    uint32_t mantissa = value & 0x03ffu;
    uint32_t bits;
    if (exponent == 0) {
        if (mantissa == 0) {
            bits = sign;
        } else {
            uint32_t adjusted_exponent = 113;
            while ((mantissa & 0x0400u) == 0) {
                mantissa <<= 1;
                --adjusted_exponent;
            }
            mantissa &= 0x03ffu;
            bits = sign | (adjusted_exponent << 23) | (mantissa << 13);
        }
    } else if (exponent == 0x1fu) {
        bits = sign | 0x7f800000u | (mantissa << 13);
    } else {
        bits = sign | ((exponent + 112u) << 23) | (mantissa << 13);
    }
    float result;
    std::memcpy(&result, &bits, sizeof(result));
    return result;
}

struct fp16_logit_reader {
    const uint16_t * values;

    float operator()(size_t index) const {
        return fp16_to_fp32(values[index]);
    }
};

struct fp32_logit_reader {
    const float * values;

    float operator()(size_t index) const {
        return values[index];
    }
};

bool fill_fp16_logits(
    float * destination,
    const uint16_t * source,
    size_t count,
    float * block_maxima) {
#if defined(__aarch64__) && defined(__ARM_FEATURE_FP16_VECTOR_ARITHMETIC)
    const uint16x8_t exponent_mask = vdupq_n_u16(0x7c00u);
    const uint16x8_t mantissa_mask = vdupq_n_u16(0x03ffu);
    const uint16x8_t negative_infinity = vdupq_n_u16(0xfc00u);
    const uint16x8_t zero = vdupq_n_u16(0u);
    for (size_t block_start = 0; block_start < count; block_start += top_k_block_size) {
        const size_t block_count = std::min(top_k_block_size, count - block_start);
        size_t offset = 0;
        float32x4_t vector_maximum =
            vdupq_n_f32(-std::numeric_limits<float>::infinity());
        for (; offset + 8 <= block_count; offset += 8) {
            const size_t index = block_start + offset;
            const uint16x8_t raw = vld1q_u16(source + index);
            const uint16x8_t exponent_is_all_ones =
                vceqq_u16(vandq_u16(raw, exponent_mask), exponent_mask);
            const uint16x8_t mantissa_is_nonzero =
                vmvnq_u16(vceqq_u16(vandq_u16(raw, mantissa_mask), zero));
            const uint16x8_t is_nan =
                vandq_u16(exponent_is_all_ones, mantissa_is_nonzero);
            const uint16x8_t unusable =
                vorrq_u16(is_nan, vceqq_u16(raw, negative_infinity));
            const uint16x8_t sanitized =
                vbslq_u16(unusable, negative_infinity, raw);
            const float16x8_t half_values = vreinterpretq_f16_u16(sanitized);
            const float32x4_t low = vcvt_f32_f16(vget_low_f16(half_values));
            const float32x4_t high = vcvt_f32_f16(vget_high_f16(half_values));
            vector_maximum = vmaxq_f32(vector_maximum, low);
            vector_maximum = vmaxq_f32(vector_maximum, high);
            vst1q_f32(destination + index, low);
            vst1q_f32(destination + index + 4, high);
        }

        float maximum = vmaxvq_f32(vector_maximum);
        for (; offset < block_count; ++offset) {
            const size_t index = block_start + offset;
            const float raw_value = fp16_to_fp32(source[index]);
            const float value = std::isnan(raw_value)
                ? -std::numeric_limits<float>::infinity()
                : raw_value;
            destination[index] = value;
            maximum = std::max(maximum, value);
        }
        block_maxima[block_start / top_k_block_size] = maximum;
    }
    return true;
#else
    for (size_t block_start = 0; block_start < count; block_start += top_k_block_size) {
        const size_t block_count = std::min(top_k_block_size, count - block_start);
        float maximum = -std::numeric_limits<float>::infinity();
        for (size_t offset = 0; offset < block_count; ++offset) {
            const size_t index = block_start + offset;
            const float raw_value = fp16_to_fp32(source[index]);
            const float value = std::isnan(raw_value)
                ? -std::numeric_limits<float>::infinity()
                : raw_value;
            destination[index] = value;
            maximum = std::max(maximum, value);
        }
        block_maxima[block_start / top_k_block_size] = maximum;
    }
    return true;
#endif
}

bool fill_masked_fp16_logits(
    float * destination,
    const uint16_t * source,
    size_t count,
    const int32_t * bitmask,
    float * block_maxima,
    size_t * allowed_count) {
    const float negative_infinity = -std::numeric_limits<float>::infinity();
    *allowed_count = 0;
#if defined(__aarch64__) && defined(__ARM_FEATURE_FP16_VECTOR_ARITHMETIC)
    const uint16x8_t exponent_mask = vdupq_n_u16(0x7c00u);
    const uint16x8_t mantissa_mask = vdupq_n_u16(0x03ffu);
    const uint16x8_t positive_infinity = vdupq_n_u16(0x7c00u);
    const uint16x8_t zero_u16 = vdupq_n_u16(0u);
    const uint32x4_t zero_u32 = vdupq_n_u32(0u);
    const float32x4_t negative_infinity_vector = vdupq_n_f32(negative_infinity);
    const uint16_t lane_bit_values[] = {1u, 2u, 4u, 8u, 16u, 32u, 64u, 128u};
    const uint16x8_t lane_bits = vld1q_u16(lane_bit_values);

    for (size_t block_start = 0; block_start < count; block_start += top_k_block_size) {
        const size_t block_count = std::min(top_k_block_size, count - block_start);
        size_t offset = 0;
        float32x4_t vector_maximum = negative_infinity_vector;
        for (; offset + 8 <= block_count; offset += 8) {
            const size_t index = block_start + offset;
            const uint32_t mask_word = static_cast<uint32_t>(bitmask[index >> 5]);
            const uint32_t allowed_bits = (mask_word >> (index & 31u)) & 0xffu;
            *allowed_count += static_cast<size_t>(__builtin_popcount(allowed_bits));
            if (allowed_bits == 0) {
                vst1q_f32(destination + index, negative_infinity_vector);
                vst1q_f32(destination + index + 4, negative_infinity_vector);
                continue;
            }

            const uint16x8_t raw = vld1q_u16(source + index);
            const uint16x8_t exponent_is_all_ones =
                vceqq_u16(vandq_u16(raw, exponent_mask), exponent_mask);
            const uint16x8_t mantissa_is_nonzero =
                vmvnq_u16(vceqq_u16(vandq_u16(raw, mantissa_mask), zero_u16));
            const uint16x8_t invalid = vorrq_u16(
                vandq_u16(exponent_is_all_ones, mantissa_is_nonzero),
                vceqq_u16(raw, positive_infinity));

            uint16x8_t allowed_lanes = vdupq_n_u16(0xffffu);
            if (allowed_bits != 0xffu) {
                allowed_lanes = vtstq_u16(
                    vdupq_n_u16(static_cast<uint16_t>(allowed_bits)), lane_bits);
            }
            if (vmaxvq_u16(vandq_u16(invalid, allowed_lanes)) != 0) {
                return false;
            }

            const float16x8_t half_values = vreinterpretq_f16_u16(raw);
            float32x4_t low = vcvt_f32_f16(vget_low_f16(half_values));
            float32x4_t high = vcvt_f32_f16(vget_high_f16(half_values));
            if (allowed_bits != 0xffu) {
                const uint32x4_t low_allowed = vcgtq_u32(
                    vmovl_u16(vget_low_u16(allowed_lanes)), zero_u32);
                const uint32x4_t high_allowed = vcgtq_u32(
                    vmovl_u16(vget_high_u16(allowed_lanes)), zero_u32);
                low = vbslq_f32(low_allowed, low, negative_infinity_vector);
                high = vbslq_f32(high_allowed, high, negative_infinity_vector);
            }
            vector_maximum = vmaxq_f32(vector_maximum, low);
            vector_maximum = vmaxq_f32(vector_maximum, high);
            vst1q_f32(destination + index, low);
            vst1q_f32(destination + index + 4, high);
        }

        float maximum = vmaxvq_f32(vector_maximum);
        for (; offset < block_count; ++offset) {
            const size_t index = block_start + offset;
            const uint32_t word = static_cast<uint32_t>(bitmask[index >> 5]);
            const bool allowed = ((word >> (index & 31u)) & 1u) != 0;
            if (!allowed) {
                destination[index] = negative_infinity;
                continue;
            }
            ++*allowed_count;
            const float value = fp16_to_fp32(source[index]);
            if (std::isnan(value) || value == std::numeric_limits<float>::infinity()) {
                return false;
            }
            destination[index] = value;
            maximum = std::max(maximum, value);
        }
        block_maxima[block_start / top_k_block_size] = maximum;
    }
    return true;
#else
    for (size_t block_start = 0; block_start < count; block_start += top_k_block_size) {
        const size_t block_count = std::min(top_k_block_size, count - block_start);
        float maximum = negative_infinity;
        for (size_t offset = 0; offset < block_count; ++offset) {
            const size_t index = block_start + offset;
            const uint32_t word = static_cast<uint32_t>(bitmask[index >> 5]);
            const bool allowed = ((word >> (index & 31u)) & 1u) != 0;
            if (!allowed) {
                destination[index] = negative_infinity;
                continue;
            }
            ++*allowed_count;
            const float value = fp16_to_fp32(source[index]);
            if (std::isnan(value) || value == std::numeric_limits<float>::infinity()) {
                return false;
            }
            destination[index] = value;
            maximum = std::max(maximum, value);
        }
        block_maxima[block_start / top_k_block_size] = maximum;
    }
    return true;
#endif
}

size_t count_allowed_tokens(const int32_t * bitmask, size_t count) {
    const size_t full_words = count / 32;
    size_t allowed = 0;
    for (size_t index = 0; index < full_words; ++index) {
        allowed += static_cast<size_t>(
            __builtin_popcount(static_cast<uint32_t>(bitmask[index])));
    }
    const size_t remainder = count & 31u;
    if (remainder != 0) {
        const uint32_t tail_mask = (uint32_t{1} << remainder) - 1u;
        allowed += static_cast<size_t>(__builtin_popcount(
            static_cast<uint32_t>(bitmask[full_words]) & tail_mask));
    }
    return allowed;
}

bool scan_fp16_block_maxima(
    const uint16_t * source,
    size_t count,
    float * block_maxima) {
#if defined(__aarch64__) && defined(__ARM_FEATURE_FP16_VECTOR_ARITHMETIC)
    const uint16x8_t exponent_mask = vdupq_n_u16(0x7c00u);
    const uint16x8_t mantissa_mask = vdupq_n_u16(0x03ffu);
    const uint16x8_t negative_infinity_bits = vdupq_n_u16(0xfc00u);
    const uint16x8_t zero = vdupq_n_u16(0u);
    const float16x8_t negative_infinity =
        vreinterpretq_f16_u16(vdupq_n_u16(0xfc00u));
    for (size_t block_start = 0; block_start < count; block_start += top_k_block_size) {
        const size_t block_count = std::min(top_k_block_size, count - block_start);
        size_t offset = 0;
        float16x8_t maximum_vector = negative_infinity;
        for (; offset + 8 <= block_count; offset += 8) {
            const uint16x8_t raw = vld1q_u16(source + block_start + offset);
            const uint16x8_t exponent_is_all_ones =
                vceqq_u16(vandq_u16(raw, exponent_mask), exponent_mask);
            const uint16x8_t mantissa_is_nonzero =
                vmvnq_u16(vceqq_u16(vandq_u16(raw, mantissa_mask), zero));
            const uint16x8_t is_nan =
                vandq_u16(exponent_is_all_ones, mantissa_is_nonzero);
            const uint16x8_t unusable =
                vorrq_u16(is_nan, vceqq_u16(raw, negative_infinity_bits));
            maximum_vector = vmaxq_f16(
                maximum_vector,
                vreinterpretq_f16_u16(vbslq_u16(
                    unusable, negative_infinity_bits, raw)));
        }

        float maximum = static_cast<float>(vmaxvq_f16(maximum_vector));
        for (; offset < block_count; ++offset) {
            const float raw_value = fp16_to_fp32(source[block_start + offset]);
            const float value = std::isnan(raw_value)
                ? -std::numeric_limits<float>::infinity()
                : raw_value;
            maximum = std::max(maximum, value);
        }
        block_maxima[block_start / top_k_block_size] = maximum;
    }
    return true;
#else
    for (size_t block_start = 0; block_start < count; block_start += top_k_block_size) {
        const size_t block_count = std::min(top_k_block_size, count - block_start);
        float maximum = -std::numeric_limits<float>::infinity();
        for (size_t offset = 0; offset < block_count; ++offset) {
            const float raw_value = fp16_to_fp32(source[block_start + offset]);
            const float value = std::isnan(raw_value)
                ? -std::numeric_limits<float>::infinity()
                : raw_value;
            maximum = std::max(maximum, value);
        }
        block_maxima[block_start / top_k_block_size] = maximum;
    }
    return true;
#endif
}

bool scan_masked_fp16_block_maxima(
    const uint16_t * source,
    size_t count,
    const int32_t * bitmask,
    float * block_maxima) {
#if defined(__aarch64__) && defined(__ARM_FEATURE_FP16_VECTOR_ARITHMETIC)
    const uint16x8_t exponent_mask = vdupq_n_u16(0x7c00u);
    const uint16x8_t mantissa_mask = vdupq_n_u16(0x03ffu);
    const uint16x8_t positive_infinity = vdupq_n_u16(0x7c00u);
    const uint16x8_t zero = vdupq_n_u16(0u);
    const uint16x8_t negative_infinity_bits = vdupq_n_u16(0xfc00u);
    const float16x8_t negative_infinity_vector =
        vreinterpretq_f16_u16(negative_infinity_bits);
    const uint16_t lane_bit_values[] = {1u, 2u, 4u, 8u, 16u, 32u, 64u, 128u};
    const uint16x8_t lane_bits = vld1q_u16(lane_bit_values);

    for (size_t block_start = 0; block_start < count; block_start += top_k_block_size) {
        const size_t block_count = std::min(top_k_block_size, count - block_start);
        size_t offset = 0;
        float16x8_t maximum_vector = negative_infinity_vector;
        for (; offset + 8 <= block_count; offset += 8) {
            const size_t token_id = block_start + offset;
            const uint32_t mask_word = static_cast<uint32_t>(bitmask[token_id >> 5]);
            const uint32_t allowed_bits = (mask_word >> (token_id & 31u)) & 0xffu;
            if (allowed_bits == 0) {
                continue;
            }

            const uint16x8_t raw = vld1q_u16(source + token_id);
            uint16x8_t allowed_lanes = vdupq_n_u16(0xffffu);
            if (allowed_bits != 0xffu) {
                allowed_lanes = vtstq_u16(
                    vdupq_n_u16(static_cast<uint16_t>(allowed_bits)), lane_bits);
            }
            const uint16x8_t exponent_is_all_ones =
                vceqq_u16(vandq_u16(raw, exponent_mask), exponent_mask);
            const uint16x8_t mantissa_is_nonzero =
                vmvnq_u16(vceqq_u16(vandq_u16(raw, mantissa_mask), zero));
            const uint16x8_t invalid = vorrq_u16(
                vandq_u16(exponent_is_all_ones, mantissa_is_nonzero),
                vceqq_u16(raw, positive_infinity));
            if (vmaxvq_u16(vandq_u16(invalid, allowed_lanes)) != 0) {
                return false;
            }
            const uint16x8_t masked =
                vbslq_u16(allowed_lanes, raw, negative_infinity_bits);
            maximum_vector =
                vmaxq_f16(maximum_vector, vreinterpretq_f16_u16(masked));
        }

        float maximum = static_cast<float>(vmaxvq_f16(maximum_vector));
        for (; offset < block_count; ++offset) {
            const size_t token_id = block_start + offset;
            const uint32_t word = static_cast<uint32_t>(bitmask[token_id >> 5]);
            if (((word >> (token_id & 31u)) & 1u) == 0) {
                continue;
            }
            const float value = fp16_to_fp32(source[token_id]);
            if (std::isnan(value) || value == std::numeric_limits<float>::infinity()) {
                return false;
            }
            maximum = std::max(maximum, value);
        }
        block_maxima[block_start / top_k_block_size] = maximum;
    }
    return true;
#else
    const float negative_infinity = -std::numeric_limits<float>::infinity();
    for (size_t block_start = 0; block_start < count; block_start += top_k_block_size) {
        const size_t block_count = std::min(top_k_block_size, count - block_start);
        float maximum = negative_infinity;
        for (size_t offset = 0; offset < block_count; ++offset) {
            const size_t token_id = block_start + offset;
            const uint32_t word = static_cast<uint32_t>(bitmask[token_id >> 5]);
            if (((word >> (token_id & 31u)) & 1u) == 0) {
                continue;
            }
            const float value = fp16_to_fp32(source[token_id]);
            if (std::isnan(value) || value == std::numeric_limits<float>::infinity()) {
                return false;
            }
            maximum = std::max(maximum, value);
        }
        block_maxima[block_start / top_k_block_size] = maximum;
    }
    return true;
#endif
}

float block_max_logit(const float * values, size_t count) {
    float maximum = -std::numeric_limits<float>::infinity();
#if defined(__aarch64__)
    size_t index = 0;
    float32x4_t vector_maximum = vdupq_n_f32(maximum);
    for (; index + 4 <= count; index += 4) {
        vector_maximum = vmaxq_f32(vector_maximum, vld1q_f32(values + index));
    }
    maximum = vmaxvq_f32(vector_maximum);
    for (; index < count; ++index) {
        maximum = std::max(maximum, values[index]);
    }
#else
    for (size_t index = 0; index < count; ++index) {
        maximum = std::max(maximum, values[index]);
    }
#endif
    return maximum;
}

bool higher_logit(const candidate & left, const candidate & right) {
    if (left.logit != right.logit) {
        return left.logit > right.logit;
    }
    return left.id < right.id;
}

bool lower_logit(const candidate & left, const candidate & right) {
    if (left.logit != right.logit) {
        return left.logit < right.logit;
    }
    return left.id > right.id;
}

uint32_t make_seed(int64_t configured_seed) {
    constexpr uint32_t default_seed = 0xffffffffu;
    const uint32_t seed = static_cast<uint32_t>(configured_seed);
    if (configured_seed >= 0 && seed != default_seed) {
        return seed;
    }
    static const bool random_device_is_prng = std::random_device().entropy() == 0;
    if (random_device_is_prng) {
        return static_cast<uint32_t>(
            std::chrono::system_clock::now().time_since_epoch().count());
    }
    std::random_device device;
    return device();
}

void validate_params(const claw_sampling_params & params) {
    if (!std::isfinite(params.temperature) || params.temperature < 0.0f) {
        throw std::invalid_argument("temperature must be finite and >= 0");
    }
    if (!std::isfinite(params.top_p) || params.top_p < 0.0f || params.top_p > 1.0f) {
        throw std::invalid_argument("top_p must be finite and in [0, 1]");
    }
    if (params.top_k < 0) {
        throw std::invalid_argument("top_k must be >= 0");
    }
    if (!std::isfinite(params.repeat_penalty) || params.repeat_penalty <= 0.0f) {
        throw std::invalid_argument("repeat_penalty must be finite and > 0");
    }
    if (!std::isfinite(params.frequency_penalty)) {
        throw std::invalid_argument("frequency_penalty must be finite");
    }
    if (!std::isfinite(params.presence_penalty)) {
        throw std::invalid_argument("presence_penalty must be finite");
    }
    if (params.repeat_last_n < 0) {
        throw std::invalid_argument("repeat_last_n must be >= 0");
    }
}

}  // namespace

struct sampling_engine_impl {
    size_t vocab_size;
    xgrammar::TokenizerInfo tokenizer_info;
    xgrammar::GrammarCompiler compiler;
    std::mutex compiler_mutex;

    sampling_engine_impl(
        std::vector<std::string> vocab,
        std::optional<std::vector<int32_t>> stop_tokens)
        : vocab_size(vocab.size()),
          tokenizer_info(
              vocab,
              xgrammar::VocabType::RAW,
              static_cast<int>(vocab.size()),
              std::move(stop_tokens)),
          compiler(tokenizer_info, 8, true, -1) {}

    xgrammar::CompiledGrammar compile(const std::string & structural_tag_json) {
        std::lock_guard<std::mutex> lock(compiler_mutex);
        auto grammar = xgrammar::Grammar::FromStructuralTag(structural_tag_json, tokenizer_info);
        if (std::holds_alternative<xgrammar::StructuralTagError>(grammar)) {
            const auto & errors = std::get<xgrammar::StructuralTagError>(grammar);
            std::visit([](const auto & error) { throw std::invalid_argument(error.what()); }, errors);
        }
        return compiler.CompileGrammar(std::get<xgrammar::Grammar>(grammar));
    }
};

struct claw_sampling_engine {
    std::shared_ptr<sampling_engine_impl> impl;
};

struct claw_sampling_session {
    std::shared_ptr<sampling_engine_impl> engine;
    claw_sampling_params params;
    std::optional<xgrammar::CompiledGrammar> compiled_grammar;
    std::optional<xgrammar::GrammarMatcher> matcher;
    std::string tool_call_tail;
    std::string tool_call_scan_buffer;
    std::vector<int32_t> bitmask;
    int64_t bitmask_shape[1];
    DLTensor bitmask_tensor{};
    bool grammar_completed = false;
    std::mt19937 rng;
    // Fixed-capacity per-session ring. Its storage is allocated once during
    // session creation, so accepting a token never grows or shrinks history.
    std::vector<int32_t> history_ring;
    size_t history_head = 0;
    size_t history_size = 0;
    // Sorted by token_id. Counts make the repeat window ready for llama.cpp
    // frequency/presence penalties without adding a hash lookup to sampling.
    std::vector<repeat_entry> active_repeat_tokens;
    std::vector<float> logit_buffer;
    std::vector<float> block_maxima;
    std::vector<candidate> candidates;
    std::vector<candidate> block_candidates;
    std::array<candidate, top_k_block_size> block_winners{};
    std::vector<candidate> sort_buffer;
    bool candidates_sorted = false;

    claw_sampling_session(
        std::shared_ptr<sampling_engine_impl> engine_value,
        const claw_sampling_params & params_value,
        std::optional<xgrammar::CompiledGrammar> grammar)
        : engine(std::move(engine_value)),
          params(params_value),
          compiled_grammar(std::move(grammar)),
          bitmask(static_cast<size_t>(xgrammar::GetBitmaskSize(
              static_cast<int>(engine->vocab_size)))),
          bitmask_shape{static_cast<int64_t>(bitmask.size())},
          rng(make_seed(params.seed)),
          history_ring(static_cast<size_t>(std::max(params.repeat_last_n, 0))),
          logit_buffer(engine->vocab_size),
          block_maxima(
              (engine->vocab_size + top_k_block_size - 1) / top_k_block_size) {
        tool_call_tail.reserve(tool_call_open_size - 1);
        tool_call_scan_buffer.reserve(64);
        const size_t requested_top_k = params.top_k > 0
            ? std::min(static_cast<size_t>(params.top_k), engine->vocab_size)
            : engine->vocab_size;
        candidates.reserve(requested_top_k);
        block_candidates.reserve(std::min<size_t>(requested_top_k, 128));
        sort_buffer.reserve(requested_top_k);
        active_repeat_tokens.reserve(static_cast<size_t>(std::max(params.repeat_last_n, 0)));
        bitmask_tensor.data = bitmask.data();
        bitmask_tensor.device = DLDevice{kDLCPU, 0};
        bitmask_tensor.ndim = 1;
        bitmask_tensor.dtype = xgrammar::GetBitmaskDLType();
        bitmask_tensor.shape = bitmask_shape;
        bitmask_tensor.strides = nullptr;
        bitmask_tensor.byte_offset = 0;
    }

    bool is_allowed(size_t token_id, bool mask_applied) const {
        if (!mask_applied) {
            return true;
        }
        const uint32_t word = static_cast<uint32_t>(bitmask[token_id >> 5]);
        return ((word >> (token_id & 31u)) & 1u) != 0;
    }

    float apply_penalties(float logit, int32_t token_id, uint32_t count) const {
        if (params.repeat_last_n == 0) {
            return logit;
        }
        if (params.penalize_newline == 0 && params.newline_token_id >= 0 &&
            token_id == params.newline_token_id) {
            return logit;
        }
        if (params.repeat_penalty != 1.0f) {
            logit = logit <= 0.0f
                ? logit * params.repeat_penalty
                : logit / params.repeat_penalty;
        }
        return logit - static_cast<float>(count) * params.frequency_penalty
            - params.presence_penalty;
    }

    float apply_penalties_for_top_k(
        uint16_t raw, const repeat_entry & entry) const {
        return apply_penalties(fp16_to_fp32(raw), entry.token_id, entry.count);
    }

    void seed_repeat_top_k(
        const uint16_t * logits,
        size_t keep) {
        candidates.clear();
        for (const repeat_entry & entry : active_repeat_tokens) {
            const uint16_t raw = logits[static_cast<size_t>(entry.token_id)];
            const float source = fp16_to_fp32(raw);
            if (std::isnan(source) ||
                source == -std::numeric_limits<float>::infinity()) {
                continue;
            }
            insert_fused_top_k(
                candidate{
                    entry.token_id,
                    apply_penalties_for_top_k(raw, entry),
                    0.0f},
                keep);
        }
    }

    void seed_masked_repeat_top_k(
        const uint16_t * logits,
        size_t keep) {
        candidates.clear();
        for (const repeat_entry & entry : active_repeat_tokens) {
            const size_t token_id = static_cast<size_t>(entry.token_id);
            if (!is_allowed(token_id, true)) {
                continue;
            }
            const uint16_t raw = logits[token_id];
            const float source = fp16_to_fp32(raw);
            if (std::isnan(source) ||
                source == std::numeric_limits<float>::infinity()) {
                throw std::runtime_error(
                    "RKNN logits contain NaN or positive infinity");
            }
            if (source == -std::numeric_limits<float>::infinity()) {
                continue;
            }
            insert_fused_top_k(
                candidate{
                    entry.token_id,
                    apply_penalties_for_top_k(raw, entry),
                    0.0f},
                keep);
        }
    }

    void merge_block_winners(size_t winner_count, size_t keep) {
        if (winner_count <= direct_global_winner_limit) {
            for (size_t index = 0; index < winner_count; ++index) {
                insert_fused_top_k(block_winners[index], keep);
            }
            return;
        }

        block_candidates.clear();
        const size_t local_keep = std::min(keep, winner_count);
        for (size_t index = 0; index < winner_count; ++index) {
            insert_top_k(block_candidates, block_winners[index], local_keep);
        }
        for (const candidate & value : block_candidates) {
            insert_fused_top_k(value, keep);
        }
    }

    // Exact small-K path for unmasked RKNN FP16 logits. Repeated IDs are
    // seeded as FP32 candidates and excluded from the dense FP16 scan. Each
    // 128-token block first produces an FP16 upper bound; only competitive
    // blocks enter the 16-token FP32 NEON threshold screen.
    void apply_unmasked_block_fp16_top_k(
        const uint16_t * logits,
        size_t keep) {
        seed_repeat_top_k(logits, keep);

        size_t repeat_index = 0;
        for (size_t block_start = 0; block_start < engine->vocab_size;
             block_start += top_k_block_size) {
            const size_t block_count =
                std::min(top_k_block_size, engine->vocab_size - block_start);
            uint32_t allowed_words[4] = {0xffffffffu, 0xffffffffu, 0xffffffffu, 0xffffffffu};
            if (block_count < top_k_block_size) {
                const size_t full_words = block_count / 32;
                const size_t remainder = block_count & 31u;
                for (size_t word = full_words + (remainder != 0); word < 4; ++word) {
                    allowed_words[word] = 0;
                }
                if (remainder != 0) {
                    allowed_words[full_words] =
                        (uint32_t{1} << remainder) - 1u;
                }
            }
            while (repeat_index < active_repeat_tokens.size() &&
                   active_repeat_tokens[repeat_index].token_id <
                       static_cast<int32_t>(block_start + block_count)) {
                const int32_t repeat_id = active_repeat_tokens[repeat_index].token_id;
                if (repeat_id >= static_cast<int32_t>(block_start)) {
                    const size_t relative =
                        static_cast<size_t>(repeat_id) - block_start;
                    allowed_words[relative >> 5] &=
                        ~(uint32_t{1} << (relative & 31u));
                }
                ++repeat_index;
            }

            float block_maximum = -std::numeric_limits<float>::infinity();
#if defined(__aarch64__) && defined(__ARM_FEATURE_FP16_VECTOR_ARITHMETIC)
            const uint16x8_t exponent_mask = vdupq_n_u16(0x7c00u);
            const uint16x8_t mantissa_mask = vdupq_n_u16(0x03ffu);
            const uint16x8_t negative_infinity_bits = vdupq_n_u16(0xfc00u);
            const uint16x8_t zero = vdupq_n_u16(0u);
            const uint16_t lane_values[] = {1u, 2u, 4u, 8u, 16u, 32u, 64u, 128u};
            const uint16x8_t lane_bits = vld1q_u16(lane_values);
            float16x8_t maxima[4] = {
                vreinterpretq_f16_u16(negative_infinity_bits),
                vreinterpretq_f16_u16(negative_infinity_bits),
                vreinterpretq_f16_u16(negative_infinity_bits),
                vreinterpretq_f16_u16(negative_infinity_bits),
            };

            size_t maximum_offset = 0;
            for (; maximum_offset + 32 <= block_count; maximum_offset += 32) {
                const uint32_t allowed_word = allowed_words[maximum_offset >> 5];
                for (size_t group = 0; group < 4; ++group) {
                    const uint32_t group_bits = (allowed_word >> (group * 8)) & 0xffu;
                    if (group_bits == 0) {
                        continue;
                    }
                    const uint16x8_t raw =
                        vld1q_u16(logits + block_start + maximum_offset + group * 8);
                    const uint16x8_t exponent_is_all_ones =
                        vceqq_u16(vandq_u16(raw, exponent_mask), exponent_mask);
                    const uint16x8_t mantissa_is_nonzero = vmvnq_u16(
                        vceqq_u16(vandq_u16(raw, mantissa_mask), zero));
                    const uint16x8_t is_nan =
                        vandq_u16(exponent_is_all_ones, mantissa_is_nonzero);
                    uint16x8_t valid = vmvnq_u16(vorrq_u16(
                        is_nan, vceqq_u16(raw, negative_infinity_bits)));
                    if (group_bits != 0xffu) {
                        valid = vandq_u16(
                            valid,
                            vtstq_u16(
                                vdupq_n_u16(static_cast<uint16_t>(group_bits)),
                                lane_bits));
                    }
                    maxima[group] = vmaxq_f16(
                        maxima[group],
                        vreinterpretq_f16_u16(vbslq_u16(
                            valid, raw, negative_infinity_bits)));
                }
            }
            float16x8_t maximum_vector = vmaxq_f16(maxima[0], maxima[1]);
            maximum_vector = vmaxq_f16(maximum_vector, maxima[2]);
            maximum_vector = vmaxq_f16(maximum_vector, maxima[3]);
            block_maximum = static_cast<float>(vmaxvq_f16(maximum_vector));
            for (; maximum_offset < block_count; ++maximum_offset) {
                if (((allowed_words[maximum_offset >> 5] >>
                      (maximum_offset & 31u)) & 1u) == 0) {
                    continue;
                }
                const float value =
                    fp16_to_fp32(logits[block_start + maximum_offset]);
                if (!std::isnan(value) &&
                    value != -std::numeric_limits<float>::infinity()) {
                    block_maximum = std::max(block_maximum, value);
                }
            }
#else
            for (size_t offset = 0; offset < block_count; ++offset) {
                if (((allowed_words[offset >> 5] >> (offset & 31u)) & 1u) == 0) {
                    continue;
                }
                const float value = fp16_to_fp32(logits[block_start + offset]);
                if (!std::isnan(value) &&
                    value != -std::numeric_limits<float>::infinity()) {
                    block_maximum = std::max(block_maximum, value);
                }
            }
#endif
            if (block_maximum == -std::numeric_limits<float>::infinity()) {
                continue;
            }
            if (candidates.size() == keep &&
                block_maximum < candidates.front().logit) {
                continue;
            }

            size_t winner_count = 0;
            size_t offset = 0;
#if defined(__aarch64__) && defined(__ARM_FEATURE_FP16_VECTOR_ARITHMETIC)
            alignas(16) float converted[16];
            for (; offset + 16 <= block_count; offset += 16) {
                const uint32_t allowed_bits =
                    (allowed_words[offset >> 5] >> (offset & 31u)) & 0xffffu;
                if (allowed_bits == 0) {
                    continue;
                }
                const float16x8_t low_half = vreinterpretq_f16_u16(
                    vld1q_u16(logits + block_start + offset));
                const float16x8_t high_half = vreinterpretq_f16_u16(
                    vld1q_u16(logits + block_start + offset + 8));
                vst1q_f32(converted, vcvt_f32_f16(vget_low_f16(low_half)));
                vst1q_f32(converted + 4, vcvt_f32_f16(vget_high_f16(low_half)));
                vst1q_f32(converted + 8, vcvt_f32_f16(vget_low_f16(high_half)));
                vst1q_f32(converted + 12, vcvt_f32_f16(vget_high_f16(high_half)));

                if (candidates.size() == keep) {
                    const float32x4_t threshold =
                        vdupq_n_f32(candidates.front().logit);
                    uint32x4_t any = vcgeq_f32(vld1q_f32(converted), threshold);
                    any = vorrq_u32(any, vcgeq_f32(vld1q_f32(converted + 4), threshold));
                    any = vorrq_u32(any, vcgeq_f32(vld1q_f32(converted + 8), threshold));
                    any = vorrq_u32(any, vcgeq_f32(vld1q_f32(converted + 12), threshold));
                    if (vmaxvq_u32(any) == 0) {
                        continue;
                    }
                }
                for (size_t lane = 0; lane < 16; ++lane) {
                    if (((allowed_bits >> lane) & 1u) == 0) {
                        continue;
                    }
                    const float value = converted[lane];
                    if (std::isnan(value) ||
                        value == -std::numeric_limits<float>::infinity()) {
                        continue;
                    }
                    const candidate current{
                        static_cast<int32_t>(block_start + offset + lane),
                        value,
                        0.0f};
                    if (candidates.size() < keep ||
                        higher_logit(current, candidates.front())) {
                        block_winners[winner_count++] = current;
                    }
                }
            }
#endif
            for (; offset < block_count; ++offset) {
                if (((allowed_words[offset >> 5] >> (offset & 31u)) & 1u) == 0) {
                    continue;
                }
                const float value = fp16_to_fp32(logits[block_start + offset]);
                if (std::isnan(value) ||
                    value == -std::numeric_limits<float>::infinity()) {
                    continue;
                }
                const candidate current{
                    static_cast<int32_t>(block_start + offset), value, 0.0f};
                if (candidates.size() < keep ||
                    higher_logit(current, candidates.front())) {
                    block_winners[winner_count++] = current;
                }
            }
            merge_block_winners(winner_count, keep);
        }

        if (candidates.empty()) {
            throw std::runtime_error("sampling produced no legal candidates");
        }
        finish_fused_top_k();
    }

    // Exact masked path for positive top-k. Legal repeated tokens are handled
    // first with FP32 repeat-penalty semantics, then removed from each block's
    // effective mask. Empty blocks are skipped, sparse blocks enumerate set
    // bits directly, and dense blocks reuse the 128-token maximum plus
    // 16-token NEON threshold screen used by the unmasked fast path.
    void apply_masked_block_fp16_top_k(
        const uint16_t * logits,
        size_t keep) {
        seed_masked_repeat_top_k(logits, keep);

        const size_t word_count = (engine->vocab_size + 31) / 32;
        size_t repeat_index = 0;
        for (size_t block_start = 0; block_start < engine->vocab_size;
             block_start += top_k_block_size) {
            const size_t block_count =
                std::min(top_k_block_size, engine->vocab_size - block_start);
            const size_t block_word_start = block_start >> 5;
            uint32_t allowed_words[4] = {0, 0, 0, 0};
            for (size_t word = 0; word < 4; ++word) {
                const size_t word_index = block_word_start + word;
                if (word_index < word_count) {
                    allowed_words[word] =
                        static_cast<uint32_t>(bitmask[word_index]);
                }
            }
            if (block_start + block_count == engine->vocab_size &&
                (engine->vocab_size & 31u) != 0) {
                const size_t last_word = (block_count - 1) >> 5;
                allowed_words[last_word] &=
                    (uint32_t{1} << (engine->vocab_size & 31u)) - 1u;
            }

            // Repeat candidates were already filtered through XGrammar and
            // inserted with their penalty. Clear their bits so the dense scan
            // cannot reinsert the original, unpenalized value.
            while (repeat_index < active_repeat_tokens.size() &&
                   active_repeat_tokens[repeat_index].token_id <
                       static_cast<int32_t>(block_start + block_count)) {
                const int32_t repeat_id = active_repeat_tokens[repeat_index].token_id;
                if (repeat_id >= static_cast<int32_t>(block_start)) {
                    const size_t relative =
                        static_cast<size_t>(repeat_id) - block_start;
                    allowed_words[relative >> 5] &=
                        ~(uint32_t{1} << (relative & 31u));
                }
                ++repeat_index;
            }

            const size_t allowed_count =
                static_cast<size_t>(__builtin_popcount(allowed_words[0])) +
                static_cast<size_t>(__builtin_popcount(allowed_words[1])) +
                static_cast<size_t>(__builtin_popcount(allowed_words[2])) +
                static_cast<size_t>(__builtin_popcount(allowed_words[3]));
            if (allowed_count == 0) {
                continue;
            }

            // Very sparse blocks are cheaper to enumerate than to load and
            // mask all 128 FP16 logits. This path also avoids a second pass.
            if (allowed_count <= sparse_mask_block_candidate_limit) {
                for (size_t word = 0; word < 4; ++word) {
                    uint32_t allowed_bits = allowed_words[word];
                    while (allowed_bits != 0) {
                        const unsigned bit =
                            static_cast<unsigned>(__builtin_ctz(allowed_bits));
                        const size_t token_id = block_start + word * 32 + bit;
                        const float value = fp16_to_fp32(logits[token_id]);
                        if (std::isnan(value) ||
                            value == std::numeric_limits<float>::infinity()) {
                            throw std::runtime_error(
                                "RKNN logits contain NaN or positive infinity");
                        }
                        if (value != -std::numeric_limits<float>::infinity()) {
                            insert_fused_top_k(
                                candidate{
                                    static_cast<int32_t>(token_id), value, 0.0f},
                                keep);
                        }
                        allowed_bits &= allowed_bits - 1u;
                    }
                }
                continue;
            }

            float block_maximum = -std::numeric_limits<float>::infinity();
#if defined(__aarch64__) && defined(__ARM_FEATURE_FP16_VECTOR_ARITHMETIC)
            const uint16x8_t exponent_mask = vdupq_n_u16(0x7c00u);
            const uint16x8_t mantissa_mask = vdupq_n_u16(0x03ffu);
            const uint16x8_t positive_infinity_bits = vdupq_n_u16(0x7c00u);
            const uint16x8_t negative_infinity_bits = vdupq_n_u16(0xfc00u);
            const uint16x8_t zero = vdupq_n_u16(0u);
            const uint16_t lane_values[] =
                {1u, 2u, 4u, 8u, 16u, 32u, 64u, 128u};
            const uint16x8_t lane_bits = vld1q_u16(lane_values);
            float16x8_t maxima[4] = {
                vreinterpretq_f16_u16(negative_infinity_bits),
                vreinterpretq_f16_u16(negative_infinity_bits),
                vreinterpretq_f16_u16(negative_infinity_bits),
                vreinterpretq_f16_u16(negative_infinity_bits),
            };

            size_t maximum_offset = 0;
            for (; maximum_offset + 32 <= block_count; maximum_offset += 32) {
                const uint32_t allowed_word = allowed_words[maximum_offset >> 5];
                for (size_t group = 0; group < 4; ++group) {
                    const uint32_t group_bits =
                        (allowed_word >> (group * 8)) & 0xffu;
                    if (group_bits == 0) {
                        continue;
                    }
                    const uint16x8_t raw = vld1q_u16(
                        logits + block_start + maximum_offset + group * 8);
                    const uint16x8_t exponent_is_all_ones =
                        vceqq_u16(vandq_u16(raw, exponent_mask), exponent_mask);
                    const uint16x8_t mantissa_is_nonzero = vmvnq_u16(
                        vceqq_u16(vandq_u16(raw, mantissa_mask), zero));
                    const uint16x8_t is_nan =
                        vandq_u16(exponent_is_all_ones, mantissa_is_nonzero);
                    const uint16x8_t allowed_lanes = group_bits == 0xffu
                        ? vdupq_n_u16(0xffffu)
                        : vtstq_u16(
                              vdupq_n_u16(static_cast<uint16_t>(group_bits)),
                              lane_bits);
                    const uint16x8_t invalid = vandq_u16(
                        allowed_lanes,
                        vorrq_u16(
                            is_nan,
                            vceqq_u16(raw, positive_infinity_bits)));
                    if (vmaxvq_u16(invalid) != 0) {
                        throw std::runtime_error(
                            "RKNN logits contain NaN or positive infinity");
                    }
                    const uint16x8_t usable = vandq_u16(
                        allowed_lanes,
                        vmvnq_u16(vorrq_u16(
                            is_nan,
                            vceqq_u16(raw, negative_infinity_bits))));
                    maxima[group] = vmaxq_f16(
                        maxima[group],
                        vreinterpretq_f16_u16(vbslq_u16(
                            usable, raw, negative_infinity_bits)));
                }
            }
            float16x8_t maximum_vector = vmaxq_f16(maxima[0], maxima[1]);
            maximum_vector = vmaxq_f16(maximum_vector, maxima[2]);
            maximum_vector = vmaxq_f16(maximum_vector, maxima[3]);
            block_maximum = static_cast<float>(vmaxvq_f16(maximum_vector));
            for (; maximum_offset < block_count; ++maximum_offset) {
                if (((allowed_words[maximum_offset >> 5] >>
                      (maximum_offset & 31u)) & 1u) == 0) {
                    continue;
                }
                const float value =
                    fp16_to_fp32(logits[block_start + maximum_offset]);
                if (std::isnan(value) ||
                    value == std::numeric_limits<float>::infinity()) {
                    throw std::runtime_error(
                        "RKNN logits contain NaN or positive infinity");
                }
                if (value != -std::numeric_limits<float>::infinity()) {
                    block_maximum = std::max(block_maximum, value);
                }
            }
#else
            for (size_t offset = 0; offset < block_count; ++offset) {
                if (((allowed_words[offset >> 5] >> (offset & 31u)) & 1u) == 0) {
                    continue;
                }
                const float value = fp16_to_fp32(logits[block_start + offset]);
                if (std::isnan(value) ||
                    value == std::numeric_limits<float>::infinity()) {
                    throw std::runtime_error(
                        "RKNN logits contain NaN or positive infinity");
                }
                if (value != -std::numeric_limits<float>::infinity()) {
                    block_maximum = std::max(block_maximum, value);
                }
            }
#endif
            if (block_maximum == -std::numeric_limits<float>::infinity()) {
                continue;
            }
            if (candidates.size() == keep &&
                block_maximum < candidates.front().logit) {
                continue;
            }

            size_t winner_count = 0;
            size_t offset = 0;
#if defined(__aarch64__) && defined(__ARM_FEATURE_FP16_VECTOR_ARITHMETIC)
            alignas(16) float converted[16];
            const uint32_t fp32_lane_values[] = {1u, 2u, 4u, 8u};
            const uint32x4_t fp32_lane_bits = vld1q_u32(fp32_lane_values);
            for (; offset + 16 <= block_count; offset += 16) {
                const uint32_t allowed_bits =
                    (allowed_words[offset >> 5] >> (offset & 31u)) & 0xffffu;
                if (allowed_bits == 0) {
                    continue;
                }
                const float16x8_t low_half = vreinterpretq_f16_u16(
                    vld1q_u16(logits + block_start + offset));
                const float16x8_t high_half = vreinterpretq_f16_u16(
                    vld1q_u16(logits + block_start + offset + 8));
                vst1q_f32(converted, vcvt_f32_f16(vget_low_f16(low_half)));
                vst1q_f32(converted + 4, vcvt_f32_f16(vget_high_f16(low_half)));
                vst1q_f32(converted + 8, vcvt_f32_f16(vget_low_f16(high_half)));
                vst1q_f32(converted + 12, vcvt_f32_f16(vget_high_f16(high_half)));

                if (candidates.size() == keep) {
                    const float32x4_t threshold =
                        vdupq_n_f32(candidates.front().logit);
                    uint32x4_t any = vdupq_n_u32(0u);
                    for (size_t group = 0; group < 4; ++group) {
                        const uint32_t group_bits =
                            (allowed_bits >> (group * 4)) & 0x0fu;
                        if (group_bits == 0) {
                            continue;
                        }
                        uint32x4_t winners = vcgeq_f32(
                            vld1q_f32(converted + group * 4), threshold);
                        if (group_bits != 0x0fu) {
                            winners = vandq_u32(
                                winners,
                                vtstq_u32(
                                    vdupq_n_u32(group_bits), fp32_lane_bits));
                        }
                        any = vorrq_u32(any, winners);
                    }
                    if (vmaxvq_u32(any) == 0) {
                        continue;
                    }
                }
                for (size_t lane = 0; lane < 16; ++lane) {
                    if (((allowed_bits >> lane) & 1u) == 0) {
                        continue;
                    }
                    const float value = converted[lane];
                    if (value == -std::numeric_limits<float>::infinity()) {
                        continue;
                    }
                    const candidate current{
                        static_cast<int32_t>(block_start + offset + lane),
                        value,
                        0.0f};
                    if (candidates.size() < keep ||
                        higher_logit(current, candidates.front())) {
                        block_winners[winner_count++] = current;
                    }
                }
            }
#endif
            for (; offset < block_count; ++offset) {
                if (((allowed_words[offset >> 5] >> (offset & 31u)) & 1u) == 0) {
                    continue;
                }
                const float value = fp16_to_fp32(logits[block_start + offset]);
                if (value == -std::numeric_limits<float>::infinity()) {
                    continue;
                }
                const candidate current{
                    static_cast<int32_t>(block_start + offset), value, 0.0f};
                if (candidates.size() < keep ||
                    higher_logit(current, candidates.front())) {
                    block_winners[winner_count++] = current;
                }
            }
            merge_block_winners(winner_count, keep);
        }

        if (candidates.empty()) {
            throw std::runtime_error(
                "XGrammar mask produced no legal sampling candidates");
        }
        finish_fused_top_k();
    }
    bool use_fused_top_k() const {
        return params.top_k > 0;
    }

    static void insert_top_k(
        std::vector<candidate> & heap,
        candidate value,
        size_t keep) {
        if (heap.size() < keep) {
            heap.push_back(value);
            std::push_heap(heap.begin(), heap.end(), higher_logit);
            return;
        }
        if (!higher_logit(value, heap.front())) {
            return;
        }
        std::pop_heap(heap.begin(), heap.end(), higher_logit);
        heap.back() = value;
        std::push_heap(heap.begin(), heap.end(), higher_logit);
    }

    void insert_fused_top_k(candidate value, size_t keep) {
        insert_top_k(candidates, value, keep);
    }

    // The block-max path handles top-k=1; generic K uses the block-local
    // FP16 threshold paths above.
    bool is_repeat_token(
        int32_t token_id,
        std::vector<repeat_entry>::const_iterator & repeat_iterator) const {
        while (repeat_iterator != active_repeat_tokens.end() &&
               repeat_iterator->token_id < token_id) {
            ++repeat_iterator;
        }
        return repeat_iterator != active_repeat_tokens.end() &&
            repeat_iterator->token_id == token_id;
    }

    void apply_sparse_mask_fp16_top_k(
        const uint16_t * logits,
        size_t keep) {
        std::vector<repeat_entry>::const_iterator repeat_iterator =
            active_repeat_tokens.cbegin();
        const size_t word_count = (engine->vocab_size + 31) / 32;
        for (size_t word_index = 0; word_index < word_count; ++word_index) {
            uint32_t allowed_bits = static_cast<uint32_t>(bitmask[word_index]);
            if (word_index + 1 == word_count && (engine->vocab_size & 31u) != 0) {
                allowed_bits &=
                    (uint32_t{1} << (engine->vocab_size & 31u)) - 1u;
            }
            while (allowed_bits != 0) {
                const unsigned bit = static_cast<unsigned>(__builtin_ctz(allowed_bits));
                const size_t token_id = word_index * 32 + bit;
                float logit = fp16_to_fp32(logits[token_id]);
                if (std::isnan(logit) || logit == std::numeric_limits<float>::infinity()) {
                    throw std::runtime_error("RKNN logits contain NaN or positive infinity");
                }
                if (is_repeat_token(static_cast<int32_t>(token_id), repeat_iterator)) {
                    logit = apply_penalties(
                        logit,
                        static_cast<int32_t>(token_id),
                        repeat_iterator->count);
                }
                insert_fused_top_k(
                    candidate{static_cast<int32_t>(token_id), logit, 0.0f}, keep);
                allowed_bits &= allowed_bits - 1u;
            }
        }
    }

    void patch_repeat_block_maxima(
        const uint16_t * logits,
        bool mask_applied) {
        if (params.repeat_last_n == 0 ||
            (params.repeat_penalty == 1.0f &&
             params.frequency_penalty == 0.0f &&
             params.presence_penalty == 0.0f)) {
            return;
        }
        for (const repeat_entry & entry : active_repeat_tokens) {
            const size_t token_id = static_cast<size_t>(entry.token_id);
            if (mask_applied && !is_allowed(token_id, true)) {
                continue;
            }
            const float penalized = apply_penalties_for_top_k(logits[token_id], entry);
            const size_t block_index = token_id / top_k_block_size;
            block_maxima[block_index] = std::max(block_maxima[block_index], penalized);
        }
    }

    void insert_fp16_block_top_k(
        const uint16_t * logits,
        size_t block_index,
        bool mask_applied,
        size_t keep) {
        const size_t block_start = block_index * top_k_block_size;
        const size_t block_count =
            std::min(top_k_block_size, engine->vocab_size - block_start);
        std::vector<repeat_entry>::const_iterator repeat_iterator = std::lower_bound(
            active_repeat_tokens.cbegin(),
            active_repeat_tokens.cend(),
            static_cast<int32_t>(block_start),
            [](const repeat_entry & entry, int32_t id) {
                return entry.token_id < id;
            });

#if defined(__aarch64__) && defined(__ARM_FEATURE_FP16_VECTOR_ARITHMETIC)
        alignas(16) float converted[8];
        size_t offset = 0;
        for (; offset + 8 <= block_count; offset += 8) {
            const size_t token_id = block_start + offset;
            uint32_t allowed_bits = 0xffu;
            if (mask_applied) {
                const uint32_t mask_word = static_cast<uint32_t>(bitmask[token_id >> 5]);
                allowed_bits = (mask_word >> (token_id & 31u)) & 0xffu;
                if (allowed_bits == 0) {
                    continue;
                }
            }
            const float16x8_t half_values =
                vreinterpretq_f16_u16(vld1q_u16(logits + token_id));
            vst1q_f32(converted, vcvt_f32_f16(vget_low_f16(half_values)));
            vst1q_f32(converted + 4, vcvt_f32_f16(vget_high_f16(half_values)));
            for (size_t lane = 0; lane < 8; ++lane) {
                if (((allowed_bits >> lane) & 1u) == 0) {
                    continue;
                }
                const int32_t id = static_cast<int32_t>(token_id + lane);
                float logit = converted[lane];
                if (std::isnan(logit) ||
                    logit == -std::numeric_limits<float>::infinity()) {
                    continue;
                }
                if (is_repeat_token(id, repeat_iterator)) {
                    logit = apply_penalties(
                        logit, id, repeat_iterator->count);
                }
                insert_fused_top_k(candidate{id, logit, 0.0f}, keep);
            }
        }
        for (; offset < block_count; ++offset) {
            const size_t token_id = block_start + offset;
            if (mask_applied && !is_allowed(token_id, true)) {
                continue;
            }
            float logit = fp16_to_fp32(logits[token_id]);
            if (std::isnan(logit) ||
                logit == -std::numeric_limits<float>::infinity()) {
                continue;
            }
            const int32_t id = static_cast<int32_t>(token_id);
            if (is_repeat_token(id, repeat_iterator)) {
                logit = apply_penalties(logit, id, repeat_iterator->count);
            }
            insert_fused_top_k(candidate{id, logit, 0.0f}, keep);
        }
#else
        for (size_t offset = 0; offset < block_count; ++offset) {
            const size_t token_id = block_start + offset;
            if (mask_applied && !is_allowed(token_id, true)) {
                continue;
            }
            float logit = fp16_to_fp32(logits[token_id]);
            if (std::isnan(logit) ||
                logit == -std::numeric_limits<float>::infinity()) {
                continue;
            }
            const int32_t id = static_cast<int32_t>(token_id);
            if (is_repeat_token(id, repeat_iterator)) {
                logit = apply_penalties(logit, id, repeat_iterator->count);
            }
            insert_fused_top_k(candidate{id, logit, 0.0f}, keep);
        }
#endif
    }

    void apply_fp16_block_top_k(
        const uint16_t * logits,
        bool mask_applied,
        size_t keep) {
        for (size_t block_index = 0; block_index < block_maxima.size(); ++block_index) {
            const float maximum = block_maxima[block_index];
            if (maximum == -std::numeric_limits<float>::infinity()) {
                continue;
            }
            // Blocks are visited in vocabulary order, so an equal maximum
            // cannot improve the established earliest-token tie break.
            if (candidates.size() == keep && maximum <= candidates.front().logit) {
                continue;
            }
            insert_fp16_block_top_k(logits, block_index, mask_applied, keep);
        }
    }

    void apply_block_top_k(size_t keep, bool cached_block_maxima) {
        constexpr size_t maximum_block_top_k = 64;
        if (keep > maximum_block_top_k) {
            for (size_t token_id = 0; token_id < engine->vocab_size; ++token_id) {
                insert_fused_top_k(
                    candidate{
                        static_cast<int32_t>(token_id),
                        logit_buffer[token_id],
                        0.0f},
                    keep);
            }
            return;
        }

        size_t block_index = 0;
        for (size_t block_start = 0; block_start < engine->vocab_size;
             block_start += top_k_block_size, ++block_index) {
            const size_t block_count =
                std::min(top_k_block_size, engine->vocab_size - block_start);
            const float local_maximum = cached_block_maxima
                ? block_maxima[block_index]
                : block_max_logit(logit_buffer.data() + block_start, block_count);
            if (local_maximum == -std::numeric_limits<float>::infinity()) {
                continue;
            }
            if (candidates.size() == keep && local_maximum <= candidates.front().logit) {
                continue;
            }

            block_candidates.clear();
            const size_t local_keep = std::min(keep, block_count);
            for (size_t offset = 0; offset < block_count; ++offset) {
                const size_t token_id = block_start + offset;
                insert_top_k(
                    block_candidates,
                    candidate{
                        static_cast<int32_t>(token_id),
                        logit_buffer[token_id],
                        0.0f},
                    local_keep);
            }

            // Reinsert in vocabulary order so equal-logit candidates keep the
            // same earliest-token behavior as the original full scan.
            std::sort(
                block_candidates.begin(),
                block_candidates.end(),
                [](const candidate & left, const candidate & right) {
                    return left.id < right.id;
                });
            for (const candidate & value : block_candidates) {
                insert_fused_top_k(value, keep);
            }
        }
    }

    void finish_fused_top_k() {
        std::sort_heap(candidates.begin(), candidates.end(), higher_logit);
        candidates_sorted = true;
    }

    static void partial_sort_candidates(
        const std::vector<candidate> & input,
        size_t count,
        std::vector<candidate> & output) {
        constexpr int bucket_count = 128;
        constexpr float bucket_low = -10.0f;
        constexpr float bucket_high = 10.0f;
        constexpr float bucket_scale = bucket_count / (bucket_high - bucket_low);
        constexpr float bucket_intercept = -bucket_low * bucket_scale;

        std::vector<int> bucket_indices;
        std::vector<size_t> histogram(bucket_count, 0);
        std::vector<candidate *> bucket_pointers;
        bucket_indices.reserve(input.size());

        for (const candidate & value : input) {
            int bucket;
            if (value.logit <= bucket_low) {
                bucket = 0;
            } else if (value.logit >= bucket_high) {
                bucket = bucket_count - 1;
            } else {
                bucket = static_cast<int>(bucket_scale * value.logit + bucket_intercept);
            }
            bucket_indices.push_back(bucket);
            ++histogram[static_cast<size_t>(bucket)];
        }

        size_t available = 0;
        int threshold_bucket = bucket_count - 1;
        for (; threshold_bucket >= 0; --threshold_bucket) {
            available += histogram[static_cast<size_t>(threshold_bucket)];
            if (available >= count) {
                break;
            }
        }

        output.resize(available);
        candidate * pointer = output.data();
        bucket_pointers.reserve(static_cast<size_t>(bucket_count - threshold_bucket));
        for (int bucket = bucket_count - 1; bucket >= threshold_bucket; --bucket) {
            bucket_pointers.push_back(pointer);
            pointer += histogram[static_cast<size_t>(bucket)];
        }
        for (size_t index = 0; index < input.size(); ++index) {
            const int bucket = bucket_indices[index];
            if (bucket >= threshold_bucket) {
                *bucket_pointers[static_cast<size_t>(bucket_count - 1 - bucket)]++ = input[index];
            }
        }

        pointer = output.data();
        size_t sorted = 0;
        for (int bucket = bucket_count - 1; bucket > threshold_bucket; --bucket) {
            const size_t bucket_size = histogram[static_cast<size_t>(bucket)];
            std::sort(pointer, pointer + bucket_size, higher_logit);
            pointer += bucket_size;
            sorted += bucket_size;
        }
        std::partial_sort(
            pointer,
            pointer + (count - sorted),
            pointer + histogram[static_cast<size_t>(threshold_bucket)],
            higher_logit);
    }

    void partial_sort_candidates_inplace(size_t count) {
        if (count <= 128) {
            std::partial_sort(
                candidates.begin(),
                candidates.begin() + static_cast<std::ptrdiff_t>(count),
                candidates.end(),
                higher_logit);
            candidates.resize(count);
            candidates_sorted = true;
            return;
        }

        partial_sort_candidates(candidates, count, sort_buffer);
        std::copy_n(sort_buffer.begin(), count, candidates.begin());
        candidates.resize(count);
        candidates_sorted = true;
    }

    void apply_top_k() {
        if (params.top_k <= 0) {
            return;
        }
        const size_t keep = std::min(static_cast<size_t>(params.top_k), candidates.size());
        if (!candidates_sorted) {
            partial_sort_candidates_inplace(keep);
        } else {
            candidates.resize(keep);
        }
    }

    void apply_softmax(bool do_sort) {
        if (do_sort && !candidates_sorted) {
            partial_sort_candidates_inplace(candidates.size());
        }

        float max_logit = candidates.front().logit;
        if (!candidates_sorted) {
            for (size_t index = 1; index < candidates.size(); ++index) {
                max_logit = std::max(max_logit, candidates[index].logit);
            }
        }
        if (!std::isfinite(max_logit)) {
            if (max_logit == std::numeric_limits<float>::infinity()) {
                size_t infinity_count = 0;
                for (const candidate & value : candidates) {
                    infinity_count += value.logit == max_logit ? 1u : 0u;
                }
                const float probability = 1.0f / static_cast<float>(infinity_count);
                for (candidate & value : candidates) {
                    value.p = value.logit == max_logit ? probability : 0.0f;
                }
                return;
            }
            throw std::runtime_error("softmax received no usable candidate logit");
        }

        float cumulative = 0.0f;
        for (candidate & value : candidates) {
            value.p = std::exp(value.logit - max_logit);
            cumulative += value.p;
        }
        for (candidate & value : candidates) {
            value.p /= cumulative;
        }
    }

    void apply_top_p() {
        if (params.top_p >= 1.0f || candidates.size() <= 1) {
            return;
        }

        apply_softmax(false);

        size_t sorted_count = candidates.size();
        candidate * sorted_data = candidates.data();
        if (!candidates_sorted && candidates.size() > 1024) {
            sorted_count = std::min<size_t>(256, candidates.size());
            partial_sort_candidates(candidates, sorted_count, sort_buffer);
            sorted_data = sort_buffer.data();
        } else if (!candidates_sorted) {
            partial_sort_candidates_inplace(candidates.size());
            sorted_data = candidates.data();
        }

        float cumulative = 0.0f;
        size_t keep = candidates.size();
        for (size_t index = 0; index < candidates.size(); ++index) {
            cumulative += sorted_data[index].p;
            if (cumulative >= params.top_p) {
                keep = index + 1;
                break;
            }

            if (!candidates_sorted && index == sorted_count - 1) {
                sorted_count = candidates.size();
                partial_sort_candidates(candidates, sorted_count, sort_buffer);
                sorted_data = sort_buffer.data();
            }
        }

        if (!candidates_sorted) {
            std::copy_n(sort_buffer.begin(), keep, candidates.begin());
            candidates_sorted = true;
        }
        candidates.resize(keep);
    }

    int32_t select_token(bool top_k_applied) {
        if (!top_k_applied) {
            apply_top_k();
        }
        // top-k=1 is already greedy. Temperature, top-p, softmax and RNG
        // cannot change the result, so avoid all downstream sampling work.
        if (params.top_k == 1) {
            return candidates.front().id;
        }
        apply_top_p();
        if (params.temperature == 0.0f) {
            return std::max_element(candidates.begin(), candidates.end(), lower_logit)->id;
        }
        if (params.temperature != 1.0f) {
            for (candidate & value : candidates) {
                value.logit /= params.temperature;
            }
        }
        if (candidates.size() == 1) {
            candidates.front().p = 1.0f;
            return candidates.front().id;
        }

        float max_logit = candidates.front().logit;
        if (!candidates_sorted) {
            for (size_t index = 1; index < candidates.size(); ++index) {
                max_logit = std::max(max_logit, candidates[index].logit);
            }
        }
        double sum = 0.0;
        if (max_logit == std::numeric_limits<float>::infinity()) {
            for (candidate & value : candidates) {
                value.p = value.logit == max_logit ? 1.0f : 0.0f;
                sum += value.p;
            }
        } else {
            if (!std::isfinite(max_logit)) {
                throw std::runtime_error("sampling received no usable candidate logit");
            }
            for (candidate & value : candidates) {
                value.p = std::exp(value.logit - max_logit);
                sum += value.p;
            }
        }

        std::uniform_real_distribution<double> distribution(0.0, 1.0);
        const double target = sum * distribution(rng);
        double cumulative = 0.0;
        size_t selected = candidates.size() - 1;
        bool found = false;
        for (size_t index = 0; index < candidates.size(); ++index) {
            if (!found) {
                cumulative += candidates[index].p;
                if (cumulative >= target) {
                    selected = index;
                    found = true;
                }
            }
            candidates[index].p = static_cast<float>(candidates[index].p / sum);
        }
        return candidates[selected].id;
    }

    void accept_history(int32_t token_id) {
        if (params.repeat_last_n == 0) {
            return;
        }
        auto active = std::lower_bound(
            active_repeat_tokens.begin(),
            active_repeat_tokens.end(),
            token_id,
            [](const repeat_entry & entry, int32_t id) {
                return entry.token_id < id;
            });
        if (active == active_repeat_tokens.end() || active->token_id != token_id) {
            active_repeat_tokens.insert(active, repeat_entry{token_id, 1});
        } else {
            ++active->count;
        }
        const size_t capacity = history_ring.size();
        if (history_size < capacity) {
            history_ring[(history_head + history_size) % capacity] = token_id;
            ++history_size;
        } else {
            const int32_t expired = history_ring[history_head];
            history_ring[history_head] = token_id;
            history_head = (history_head + 1) % capacity;
            auto expired_active = std::lower_bound(
                active_repeat_tokens.begin(),
                active_repeat_tokens.end(),
                expired,
                [](const repeat_entry & entry, int32_t id) {
                    return entry.token_id < id;
                });
            if (expired_active == active_repeat_tokens.end() ||
                expired_active->token_id != expired) {
                throw std::logic_error("repeat-token count is inconsistent with history");
            }
            if (--expired_active->count == 0) {
                active_repeat_tokens.erase(expired_active);
            }
        }
    }

    void advance_tool_call_matcher(int32_t token_id, claw_sampling_result * result) {
        if (!compiled_grammar.has_value() || matcher.has_value() || grammar_completed) {
            return;
        }
        const std::vector<std::string> & token_pieces =
            engine->tokenizer_info.GetDecodedVocab();
        if (token_id < 0 || static_cast<size_t>(token_id) >= token_pieces.size()) {
            throw std::runtime_error("sampled token ID is outside the vocabulary");
        }

        const std::string & piece = token_pieces[static_cast<size_t>(token_id)];
        tool_call_scan_buffer.clear();
        tool_call_scan_buffer.append(tool_call_tail);
        tool_call_scan_buffer.append(piece);
        const size_t marker_at = tool_call_scan_buffer.find(tool_call_open);
        if (marker_at == std::string::npos) {
            const size_t tail_size = std::min(
                tool_call_open_size - 1, tool_call_scan_buffer.size());
            tool_call_tail.assign(
                tool_call_scan_buffer.data() + tool_call_scan_buffer.size() - tail_size,
                tail_size);
            return;
        }

        matcher.emplace(*compiled_grammar);
        tool_call_tail.clear();
        const size_t suffix_at = marker_at + tool_call_open_size;
        const size_t suffix_size = tool_call_scan_buffer.size() - suffix_at;
        if (suffix_size > 0 && !matcher->AcceptString(
                std::string(tool_call_scan_buffer.data() + suffix_at, suffix_size))) {
            matcher.reset();
            throw std::runtime_error("XGrammar rejected content after the tool-call marker");
        }
        if (matcher->IsCompleted()) {
            matcher.reset();
            grammar_completed = true;
            result->grammar_completed = 1;
        }
    }
};

namespace {

template <typename ReadLogit>
int sample_impl(
    claw_sampling_session * session,
    ReadLogit read_logit,
    claw_sampling_result * result) {
    if (session == nullptr || result == nullptr) {
        throw std::invalid_argument("sample received a null session or result");
    }
    *result = claw_sampling_result{};
    const bool grammar_active = session->matcher.has_value();
    result->grammar_active_before = grammar_active ? 1 : 0;

    const auto mask_start = clock_type::now();
    bool mask_applied = false;
    if (grammar_active) {
        mask_applied = session->matcher->FillNextTokenBitmask(&session->bitmask_tensor);
    }
    result->mask_ms = elapsed_ms(mask_start);
    result->mask_applied = mask_applied ? 1 : 0;

    const auto sampler_start = clock_type::now();
    session->candidates.clear();
    session->candidates_sorted = false;
    const bool top_k_applied = session->use_fused_top_k();
    const size_t keep = top_k_applied
        ? std::min(static_cast<size_t>(session->params.top_k), session->engine->vocab_size)
        : 0;
    const bool penalties_enabled =
        session->params.repeat_last_n != 0 &&
        (session->params.repeat_penalty != 1.0f ||
         session->params.frequency_penalty != 0.0f ||
         session->params.presence_penalty != 0.0f);
    auto validate_logit = [](float logit) {
        if (std::isnan(logit) || logit == std::numeric_limits<float>::infinity()) {
            throw std::runtime_error("RKNN logits contain NaN or positive infinity");
        }
    };

    size_t allowed_count = 0;
    bool cached_block_maxima = false;
    bool direct_fp16_top_k = false;

    // Positive top-k on masked FP16 logits selects between three exact paths:
    // globally sparse masks enumerate legal IDs, top-k=1 reuses the measured
    // cached block-maximum path, and generic K uses the repeat-aware masked
    // block path. top-k=0 retains full materialization for exact top-p.
    if constexpr (std::is_same_v<std::decay_t<ReadLogit>, fp16_logit_reader>) {
        if (top_k_applied) {
            direct_fp16_top_k = true;
            if (mask_applied) {
                allowed_count = count_allowed_tokens(
                    session->bitmask.data(), session->engine->vocab_size);
                if (allowed_count == 0) {
                    throw std::runtime_error(
                        "XGrammar mask produced no legal sampling candidates");
                }
                if (allowed_count <= sparse_mask_candidate_limit) {
                    // A globally sparse mask is cheaper as one direct ctz
                    // pass. Each legal token is visited exactly once, so its
                    // repeat penalty is applied inline without a second mask.
                    session->apply_sparse_mask_fp16_top_k(
                        read_logit.values, keep);
                    session->finish_fused_top_k();
                } else if (keep == 1) {
                    // For greedy top-k, the existing cached block maxima path
                    // is faster on real XGrammar masks than constructing an
                    // effective mask and local heap for every dense block.
                    if (!scan_masked_fp16_block_maxima(
                            read_logit.values,
                            session->engine->vocab_size,
                            session->bitmask.data(),
                            session->block_maxima.data())) {
                        throw std::runtime_error(
                            "RKNN logits contain NaN or positive infinity");
                    }
                    session->patch_repeat_block_maxima(read_logit.values, true);
                    session->apply_fp16_block_top_k(
                        read_logit.values, true, keep);
                    session->finish_fused_top_k();
                } else {
                    session->apply_masked_block_fp16_top_k(
                        read_logit.values, keep);
                }
            } else if (keep == 1) {
                if (!scan_fp16_block_maxima(
                        read_logit.values,
                        session->engine->vocab_size,
                        session->block_maxima.data())) {
                    throw std::runtime_error(
                        "RKNN logits contain NaN or positive infinity");
                }
                session->patch_repeat_block_maxima(read_logit.values, false);
                session->apply_fp16_block_top_k(read_logit.values, false, keep);
                session->finish_fused_top_k();
            } else {
                session->apply_unmasked_block_fp16_top_k(
                    read_logit.values, keep);
            }
        }
    }

    if (!direct_fp16_top_k) {
        // Full materialization fallback for FP32 input and top-k=0.
        if (mask_applied) {
            if constexpr (std::is_same_v<std::decay_t<ReadLogit>, fp16_logit_reader>) {
                if (!fill_masked_fp16_logits(
                        session->logit_buffer.data(),
                        read_logit.values,
                        session->engine->vocab_size,
                        session->bitmask.data(),
                        session->block_maxima.data(),
                        &allowed_count)) {
                    throw std::runtime_error(
                        "RKNN logits contain NaN or positive infinity");
                }
                cached_block_maxima = true;
            } else {
                for (size_t token_id = 0; token_id < session->engine->vocab_size; ++token_id) {
                    const bool allowed = session->is_allowed(token_id, true);
                    allowed_count += allowed ? 1 : 0;
                    if (!allowed) {
                        session->logit_buffer[token_id] =
                            -std::numeric_limits<float>::infinity();
                        continue;
                    }
                    const float logit = read_logit(token_id);
                    validate_logit(logit);
                    session->logit_buffer[token_id] = logit;
                }
            }
        } else {
            allowed_count = session->engine->vocab_size;
            if constexpr (std::is_same_v<std::decay_t<ReadLogit>, fp16_logit_reader>) {
                if (!fill_fp16_logits(
                        session->logit_buffer.data(),
                        read_logit.values,
                        session->engine->vocab_size,
                        session->block_maxima.data())) {
                    throw std::runtime_error(
                        "RKNN logits contain NaN or positive infinity");
                }
                cached_block_maxima = true;
            } else {
                for (size_t token_id = 0; token_id < session->engine->vocab_size; ++token_id) {
                    const float logit = read_logit(token_id);
                    validate_logit(logit);
                    session->logit_buffer[token_id] = logit;
                }
            }
        }
        if (allowed_count == 0) {
            throw std::runtime_error("XGrammar mask produced no legal sampling candidates");
        }

        // Repetition penalty is sparse on the materialized fallback.  Patch
        // only token IDs present in the configured history window.
        if (penalties_enabled) {
            for (const repeat_entry & entry : session->active_repeat_tokens) {
                const size_t token_id = static_cast<size_t>(entry.token_id);
                if (!mask_applied || session->is_allowed(token_id, true)) {
                    const float penalized =
                        session->apply_penalties(
                            session->logit_buffer[token_id],
                            entry.token_id,
                            entry.count);
                    session->logit_buffer[token_id] = penalized;
                    if (cached_block_maxima) {
                        const size_t block_index = token_id / top_k_block_size;
                        session->block_maxima[block_index] =
                            std::max(session->block_maxima[block_index], penalized);
                    }
                }
            }
        }

        if (top_k_applied) {
            session->apply_block_top_k(keep, cached_block_maxima);
            session->finish_fused_top_k();
        } else {
            for (size_t token_id = 0; token_id < session->engine->vocab_size; ++token_id) {
                session->candidates.push_back(candidate{
                    static_cast<int32_t>(token_id),
                    session->logit_buffer[token_id],
                    0.0f});
            }
        }
    }

    const int32_t token_id = session->select_token(top_k_applied);
    result->sampler_ms = elapsed_ms(sampler_start);

    const auto accept_start = clock_type::now();
    if (session->matcher.has_value()) {
        if (!session->matcher->AcceptToken(token_id)) {
            throw std::runtime_error("XGrammar rejected the sampled token");
        }
        if (session->matcher->IsCompleted()) {
            session->matcher.reset();
            session->grammar_completed = true;
            result->grammar_completed = 1;
        }
    } else {
        session->advance_tool_call_matcher(token_id, result);
    }
    session->accept_history(token_id);
    result->accept_ms = elapsed_ms(accept_start);
    result->token_id = token_id;
    result->grammar_active_after = session->matcher.has_value() ? 1 : 0;
    return 0;
}

template <typename Function>
int protect(Function && function) {
    try {
        const int result = function();
        last_error.clear();
        return result;
    } catch (const std::exception & error) {
        last_error = error.what();
    } catch (...) {
        last_error = "unknown native sampling error";
    }
    return -1;
}

}  // namespace

extern "C" {

claw_sampling_engine * claw_sampling_engine_create(
    size_t vocab_size,
    const char * const * vocab_pieces,
    const uint32_t * vocab_piece_lengths,
    const int32_t * stop_token_ids,
    size_t stop_token_count) {
    try {
        if (vocab_size == 0 || vocab_size > static_cast<size_t>(std::numeric_limits<int>::max())) {
            throw std::invalid_argument("vocab_size is outside the supported range");
        }
        if (vocab_pieces == nullptr || vocab_piece_lengths == nullptr) {
            throw std::invalid_argument("vocabulary pointers are required");
        }
        std::vector<std::string> vocab;
        vocab.reserve(vocab_size);
        for (size_t index = 0; index < vocab_size; ++index) {
            if (vocab_pieces[index] == nullptr) {
                if (vocab_piece_lengths[index] != 0) {
                    throw std::invalid_argument("vocabulary contains a null token piece");
                }
                vocab.emplace_back();
                continue;
            }
            vocab.emplace_back(vocab_pieces[index], vocab_piece_lengths[index]);
        }
        std::optional<std::vector<int32_t>> stop_tokens;
        if (stop_token_count > 0) {
            if (stop_token_ids == nullptr) {
                throw std::invalid_argument("stop token pointer is null");
            }
            stop_tokens = std::vector<int32_t>(
                stop_token_ids, stop_token_ids + stop_token_count);
            for (int32_t token_id : *stop_tokens) {
                if (token_id < 0 || static_cast<size_t>(token_id) >= vocab_size) {
                    throw std::invalid_argument("stop token ID is outside the vocabulary");
                }
            }
        }
        auto * engine = new claw_sampling_engine{
            std::make_shared<sampling_engine_impl>(std::move(vocab), std::move(stop_tokens))};
        last_error.clear();
        return engine;
    } catch (const std::exception & error) {
        last_error = error.what();
    } catch (...) {
        last_error = "unknown error while creating native sampling engine";
    }
    return nullptr;
}

void claw_sampling_engine_destroy(claw_sampling_engine * engine) {
    delete engine;
}

claw_sampling_session * claw_sampling_session_create(
    claw_sampling_engine * engine,
    const char * structural_tag_json,
    const claw_sampling_params * params) {
    try {
        if (engine == nullptr || params == nullptr) {
            throw std::invalid_argument("engine and sampling params are required");
        }
        validate_params(*params);
        std::optional<xgrammar::CompiledGrammar> grammar;
        if (structural_tag_json != nullptr && structural_tag_json[0] != '\0') {
            grammar = engine->impl->compile(structural_tag_json);
        }
        auto * session = new claw_sampling_session(engine->impl, *params, std::move(grammar));
        last_error.clear();
        return session;
    } catch (const std::exception & error) {
        last_error = error.what();
    } catch (...) {
        last_error = "unknown error while creating native sampling session";
    }
    return nullptr;
}

void claw_sampling_session_destroy(claw_sampling_session * session) {
    delete session;
}

int claw_sampling_session_sample_f16(
    claw_sampling_session * session,
    const uint16_t * logits,
    claw_sampling_result * result) {
    return protect([&]() {
        if (logits == nullptr) {
            throw std::invalid_argument("FP16 logits pointer is null");
        }
        return sample_impl(session, fp16_logit_reader{logits}, result);
    });
}

int claw_sampling_session_sample_f32(
    claw_sampling_session * session,
    const float * logits,
    claw_sampling_result * result) {
    return protect([&]() {
        if (logits == nullptr) {
            throw std::invalid_argument("FP32 logits pointer is null");
        }
        return sample_impl(session, fp32_logit_reader{logits}, result);
    });
}

const char * claw_sampling_last_error(void) {
    return last_error.c_str();
}

}  // extern "C"
