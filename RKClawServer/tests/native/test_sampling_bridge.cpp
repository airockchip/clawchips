#include "sampling_bridge.h"

#include <algorithm>
#include <cassert>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <limits>
#include <random>
#include <string>
#include <vector>

namespace {

struct reference_candidate {
    int32_t id;
    float logit;
    float p;
};

int32_t reference_llama_random_sample(
    const float * logits,
    size_t count,
    float top_p,
    float temperature,
    std::mt19937 & rng) {
    std::vector<reference_candidate> candidates;
    candidates.reserve(count);
    for (size_t index = 0; index < count; ++index) {
        candidates.push_back(reference_candidate{
            static_cast<int32_t>(index), logits[index], 0.0f});
    }
    std::sort(candidates.begin(), candidates.end(), [](const auto & left, const auto & right) {
        return left.logit > right.logit;
    });

    float probability_sum = 0.0f;
    for (reference_candidate & value : candidates) {
        value.p = std::exp(value.logit - candidates.front().logit);
        probability_sum += value.p;
    }
    float cumulative_probability = 0.0f;
    size_t keep = candidates.size();
    for (size_t index = 0; index < candidates.size(); ++index) {
        candidates[index].p /= probability_sum;
        cumulative_probability += candidates[index].p;
        if (cumulative_probability >= top_p) {
            keep = index + 1;
            break;
        }
    }
    candidates.resize(keep);

    for (reference_candidate & value : candidates) {
        value.logit /= temperature;
    }
    const float max_logit = candidates.front().logit;
    double weight_sum = 0.0;
    for (reference_candidate & value : candidates) {
        value.p = std::exp(value.logit - max_logit);
        weight_sum += value.p;
    }
    std::uniform_real_distribution<double> distribution(0.0, 1.0);
    const double target = weight_sum * distribution(rng);
    double running = 0.0;
    for (const reference_candidate & value : candidates) {
        running += value.p;
        if (running >= target) {
            return value.id;
        }
    }
    return candidates.back().id;
}

}  // namespace

int main() {
    const char * pieces[] = {"a", "b", "c", "d"};
    const uint32_t lengths[] = {1, 1, 1, 1};
    claw_sampling_engine * engine = claw_sampling_engine_create(4, pieces, lengths, nullptr, 0);
    assert(engine != nullptr);

    claw_sampling_params params{};
    params.temperature = 0.0f;
    params.top_p = 0.9f;
    params.top_k = 3;
    params.repeat_penalty = 1.1f;
    params.frequency_penalty = 0.0f;
    params.presence_penalty = 0.0f;
    params.repeat_last_n = 3;
    params.newline_token_id = -1;
    params.penalize_newline = 0;
    params.seed = 1234;
    claw_sampling_session * session = claw_sampling_session_create(engine, nullptr, &params);
    assert(session != nullptr);

    claw_sampling_result result{};
    const float first_logits[] = {0.5f, 2.2f, -2.0f, 1.0f};
    assert(claw_sampling_session_sample_f32(session, first_logits, &result) == 0);
    assert(result.token_id == 1);
    assert(result.grammar_active_before == 0);

    // Token 1 is penalized from 2.2 to 2.0, so token 2 wins the next step.
    const float second_logits[] = {0.5f, 2.2f, 2.05f, 1.0f};
    assert(claw_sampling_session_sample_f32(session, second_logits, &result) == 0);
    assert(result.token_id == 2);

    claw_sampling_session_destroy(session);

    // The FP16 entry point uses the same validation and selection semantics.
    params.repeat_penalty = 1.0f;
    params.repeat_last_n = 0;
    claw_sampling_session * fp16_session =
        claw_sampling_session_create(engine, nullptr, &params);
    assert(fp16_session != nullptr);
    const uint16_t fp16_logits[] = {0x3800u, 0x4000u, 0xc000u, 0x3c00u};
    assert(claw_sampling_session_sample_f16(fp16_session, fp16_logits, &result) == 0);
    assert(result.token_id == 1);
    const uint16_t fp16_positive_infinity[] = {0x3800u, 0x7c00u, 0xc000u, 0x3c00u};
    assert(claw_sampling_session_sample_f16(
        fp16_session, fp16_positive_infinity, &result) == 0);
    assert(result.token_id == 1);
    const uint16_t fp16_nan[] = {0x3800u, 0x7e00u, 0x4000u, 0xfc00u};
    assert(claw_sampling_session_sample_f16(fp16_session, fp16_nan, &result) == 0);
    assert(result.token_id == 2);
    claw_sampling_session_destroy(fp16_session);
    params.repeat_penalty = 1.1f;
    params.repeat_last_n = 3;

    // Repeated negative logits must become more negative. Dividing -2.0 by
    // 1.1 would incorrectly reward token 1 and make it win this step.
    params.top_k = 4;
    claw_sampling_session * negative_penalty_session =
        claw_sampling_session_create(engine, nullptr, &params);
    assert(negative_penalty_session != nullptr);
    assert(claw_sampling_session_sample_f32(
        negative_penalty_session, first_logits, &result) == 0);
    const float negative_logits[] = {-3.0f, -2.0f, -2.1f, -4.0f};
    assert(claw_sampling_session_sample_f32(
        negative_penalty_session, negative_logits, &result) == 0);
    assert(result.token_id == 2);
    claw_sampling_session_destroy(negative_penalty_session);

    // Duplicate history entries decrement one occurrence at a time. After
    // the oldest of two token-0 occurrences expires, token 0 must remain
    // repeat-penalized because one occurrence is still inside repeat_last_n.
    params.temperature = 0.0f;
    params.top_p = 1.0f;
    params.top_k = 1;
    params.repeat_penalty = 2.0f;
    params.repeat_last_n = 3;
    claw_sampling_session * repeat_count_session =
        claw_sampling_session_create(engine, nullptr, &params);
    assert(repeat_count_session != nullptr);
    const uint16_t repeat_zero[] = {0x4900u, 0x4400u, 0x0000u, 0x0000u};
    assert(claw_sampling_session_sample_f16(
        repeat_count_session, repeat_zero, &result) == 0);
    assert(result.token_id == 0);
    assert(claw_sampling_session_sample_f16(
        repeat_count_session, repeat_zero, &result) == 0);
    assert(result.token_id == 0);
    const uint16_t choose_one[] = {0x0000u, 0x4a00u, 0x0000u, 0x0000u};
    assert(claw_sampling_session_sample_f16(
        repeat_count_session, choose_one, &result) == 0);
    assert(result.token_id == 1);
    const uint16_t choose_two[] = {0x0000u, 0x0000u, 0x4a00u, 0x0000u};
    assert(claw_sampling_session_sample_f16(
        repeat_count_session, choose_two, &result) == 0);
    assert(result.token_id == 2);
    const uint16_t count_sensitive[] = {0x4900u, 0x0000u, 0x0000u, 0x4600u};
    assert(claw_sampling_session_sample_f16(
        repeat_count_session, count_sensitive, &result) == 0);
    assert(result.token_id == 3);
    claw_sampling_session_destroy(repeat_count_session);

    // Frequency penalty scales with the number of occurrences while presence
    // penalty is applied once to every token in the repeat window.
    params.top_k = 1;
    params.repeat_penalty = 1.0f;
    params.frequency_penalty = 0.5f;
    params.presence_penalty = 1.0f;
    params.repeat_last_n = 8;
    claw_sampling_session * additive_penalty_session =
        claw_sampling_session_create(engine, nullptr, &params);
    assert(additive_penalty_session != nullptr);
    const float additive_first[] = {4.0f, 3.0f, 0.0f, 0.0f};
    assert(claw_sampling_session_sample_f32(
        additive_penalty_session, additive_first, &result) == 0);
    assert(result.token_id == 0);
    const float additive_second[] = {4.4f, 4.0f, 0.0f, 0.0f};
    assert(claw_sampling_session_sample_f32(
        additive_penalty_session, additive_second, &result) == 0);
    assert(result.token_id == 1);
    const float additive_third[] = {4.4f, 4.0f, 0.0f, 0.0f};
    assert(claw_sampling_session_sample_f32(
        additive_penalty_session, additive_third, &result) == 0);
    assert(result.token_id == 0);
    claw_sampling_session_destroy(additive_penalty_session);

    params.frequency_penalty = 0.0f;
    params.presence_penalty = 0.0f;

    const char * tool_pieces[] = {"<tool_", "call>", "a", "b", "<tool_call>a"};
    const uint32_t tool_lengths[] = {6, 5, 1, 1, 12};
    claw_sampling_engine * tool_engine = claw_sampling_engine_create(
        5, tool_pieces, tool_lengths, nullptr, 0);
    assert(tool_engine != nullptr);
    const char * structure =
        "{\"type\":\"structural_tag\",\"format\":{\"type\":\"const_string\",\"value\":\"a\"}}";
    params.repeat_penalty = 1.0f;
    claw_sampling_session * grammar_session =
        claw_sampling_session_create(tool_engine, structure, &params);
    assert(grammar_session != nullptr);
    const float marker_part_one_logits[] = {9.0f, 1.0f, 0.0f, 0.0f, 0.0f};
    assert(claw_sampling_session_sample_f32(
        grammar_session, marker_part_one_logits, &result) == 0);
    assert(result.token_id == 0);
    assert(result.grammar_active_after == 0);
    const float marker_part_two_logits[] = {1.0f, 9.0f, 0.0f, 0.0f, 0.0f};
    assert(claw_sampling_session_sample_f32(
        grammar_session, marker_part_two_logits, &result) == 0);
    assert(result.token_id == 1);
    assert(result.grammar_active_before == 0);
    assert(result.grammar_active_after == 1);

    // Token 3 has the highest raw logit, but the activated grammar only
    // accepts token 2 ("a").
    const float grammar_logits[] = {1.0f, 2.0f, 3.0f, 7.0f, 6.0f};
    assert(claw_sampling_session_sample_f32(grammar_session, grammar_logits, &result) == 0);
    assert(result.token_id == 2);
    assert(result.grammar_active_before == 1);
    assert(result.mask_applied == 1);
    assert(result.grammar_completed == 1);

    claw_sampling_session_destroy(grammar_session);

    // Content following the marker in the same token is accepted immediately.
    claw_sampling_session * joined_session =
        claw_sampling_session_create(tool_engine, structure, &params);
    assert(joined_session != nullptr);
    const float joined_logits[] = {1.0f, 2.0f, 3.0f, 4.0f, 9.0f};
    assert(claw_sampling_session_sample_f32(joined_session, joined_logits, &result) == 0);
    assert(result.token_id == 4);
    assert(result.grammar_completed == 1);
    claw_sampling_session_destroy(joined_session);
    claw_sampling_engine_destroy(tool_engine);

    // The FP16 masked path skips conversion/validation for fully illegal
    // vector groups, masks mixed groups, and still rejects invalid allowed
    // logits. Token 9 is the only token accepted by the grammar.
    std::vector<std::string> masked_pieces = {
        "<tool_call>", "d1", "d2", "d3", "d4", "d5", "d6", "d7",
        "d8", "a", "d10", "d11", "d12", "d13", "d14", "d15",
    };
    std::vector<const char *> masked_piece_pointers;
    std::vector<uint32_t> masked_piece_lengths;
    for (const std::string & piece : masked_pieces) {
        masked_piece_pointers.push_back(piece.data());
        masked_piece_lengths.push_back(static_cast<uint32_t>(piece.size()));
    }
    claw_sampling_engine * masked_engine = claw_sampling_engine_create(
        masked_pieces.size(),
        masked_piece_pointers.data(),
        masked_piece_lengths.data(),
        nullptr,
        0);
    assert(masked_engine != nullptr);
    claw_sampling_session * masked_session =
        claw_sampling_session_create(masked_engine, structure, &params);
    assert(masked_session != nullptr);
    std::vector<float> masked_marker_logits(masked_pieces.size(), -10.0f);
    masked_marker_logits[0] = 10.0f;
    assert(claw_sampling_session_sample_f32(
        masked_session, masked_marker_logits.data(), &result) == 0);
    assert(result.grammar_active_after == 1);
    std::vector<uint16_t> masked_fp16_logits(masked_pieces.size(), 0x7c00u);
    masked_fp16_logits[9] = 0x3c00u;
    assert(claw_sampling_session_sample_f16(
        masked_session, masked_fp16_logits.data(), &result) == 0);
    assert(result.token_id == 9);
    assert(result.grammar_completed == 1);
    claw_sampling_session_destroy(masked_session);

    claw_sampling_session * invalid_allowed_session =
        claw_sampling_session_create(masked_engine, structure, &params);
    assert(invalid_allowed_session != nullptr);
    assert(claw_sampling_session_sample_f32(
        invalid_allowed_session, masked_marker_logits.data(), &result) == 0);
    masked_fp16_logits[9] = 0x7c00u;
    assert(claw_sampling_session_sample_f16(
        invalid_allowed_session, masked_fp16_logits.data(), &result) == -1);
    claw_sampling_session_destroy(invalid_allowed_session);
    claw_sampling_engine_destroy(masked_engine);

    // Dense masked top-k=1 uses four 32-bit words per 128-token block.
    // Token 100 is already in repeat history, so it must be inserted once
    // with its penalty and cleared from the ordinary effective mask. The
    // illegal +Inf marker must not affect validation or block maxima.
    std::vector<std::string> dense_mask_pieces(130, "a");
    dense_mask_pieces[0] = "<tool_call>";
    std::vector<const char *> dense_mask_piece_pointers;
    std::vector<uint32_t> dense_mask_piece_lengths;
    dense_mask_piece_pointers.reserve(dense_mask_pieces.size());
    dense_mask_piece_lengths.reserve(dense_mask_pieces.size());
    for (const std::string & piece : dense_mask_pieces) {
        dense_mask_piece_pointers.push_back(piece.data());
        dense_mask_piece_lengths.push_back(static_cast<uint32_t>(piece.size()));
    }
    claw_sampling_engine * dense_mask_engine = claw_sampling_engine_create(
        dense_mask_pieces.size(),
        dense_mask_piece_pointers.data(),
        dense_mask_piece_lengths.data(),
        nullptr,
        0);
    assert(dense_mask_engine != nullptr);
    params.temperature = 0.0f;
    params.top_p = 1.0f;
    params.top_k = 1;
    params.repeat_penalty = 2.0f;
    params.repeat_last_n = 3;
    claw_sampling_session * dense_mask_session =
        claw_sampling_session_create(dense_mask_engine, structure, &params);
    assert(dense_mask_session != nullptr);
    std::vector<uint16_t> dense_mask_logits(130, 0xd640u);  // -100
    dense_mask_logits[100] = 0x4900u;                       // 10
    assert(claw_sampling_session_sample_f16(
        dense_mask_session, dense_mask_logits.data(), &result) == 0);
    assert(result.token_id == 100);
    std::fill(dense_mask_logits.begin(), dense_mask_logits.end(), 0xd640u);
    dense_mask_logits[0] = 0x4d00u;  // 20, activates the grammar
    assert(claw_sampling_session_sample_f16(
        dense_mask_session, dense_mask_logits.data(), &result) == 0);
    assert(result.token_id == 0);
    assert(result.grammar_active_after == 1);
    std::fill(dense_mask_logits.begin(), dense_mask_logits.end(), 0xd640u);
    dense_mask_logits[0] = 0x7c00u;    // +Inf, but illegal under the grammar
    dense_mask_logits[100] = 0x4900u;  // 10 / 2 = 5 after repeat penalty
    dense_mask_logits[101] = 0x4880u;  // 9, therefore the exact winner
    assert(claw_sampling_session_sample_f16(
        dense_mask_session, dense_mask_logits.data(), &result) == 0);
    assert(result.mask_applied == 1);
    assert(result.token_id == 101);
    assert(result.grammar_completed == 1);
    claw_sampling_session_destroy(dense_mask_session);
    claw_sampling_engine_destroy(dense_mask_engine);

    // CoPaw/Qwen3.5 body-only structure: C++ detects <tool_call>, then an
    // open regex accepts a function name that was not enumerated by tools.
    const char * qwen35_pieces[] = {
        "<tool_call>",
        "\n<function=unregistered_2>\n",
        "<parameter=city>\n",
        "Shenzhen",
        "\n</parameter>",
        "\n</function>\n</tool_call>",
        "invalid",
    };
    const uint32_t qwen35_lengths[] = {11, 20, 17, 8, 13, 25, 7};
    claw_sampling_engine * qwen35_engine = claw_sampling_engine_create(
        7, qwen35_pieces, qwen35_lengths, nullptr, 0);
    assert(qwen35_engine != nullptr);
    const char * qwen35_structure = R"JSON({
        "type":"structural_tag",
        "format":{
            "type":"sequence",
            "elements":[
                {"type":"const_string","value":"\n<function="},
                {"type":"regex","pattern":"[A-Za-z0-9_-]{1,64}"},
                {"type":"const_string","value":">\n"},
                {
                    "type":"json_schema",
                    "json_schema":{
                        "type":"object",
                        "additionalProperties":true
                    },
                    "style":"qwen_xml"
                },
                {"type":"const_string","value":"\n</function>\n</tool_call>"}
            ]
        }
    })JSON";
    claw_sampling_session * qwen35_session =
        claw_sampling_session_create(qwen35_engine, qwen35_structure, &params);
    assert(qwen35_session != nullptr);
    float qwen35_logits[7] = {};
    qwen35_logits[0] = 10.0f;
    assert(claw_sampling_session_sample_f32(qwen35_session, qwen35_logits, &result) == 0);
    assert(result.token_id == 0);
    assert(result.grammar_active_after == 1);
    for (int32_t expected : {1, 2, 3, 4, 5}) {
        std::fill(std::begin(qwen35_logits), std::end(qwen35_logits), -100.0f);
        qwen35_logits[expected] = 10.0f;
        assert(claw_sampling_session_sample_f32(
            qwen35_session, qwen35_logits, &result) == 0);
        assert(result.token_id == expected);
    }
    assert(result.grammar_completed == 1);
    claw_sampling_session_destroy(qwen35_session);
    claw_sampling_engine_destroy(qwen35_engine);

    params.temperature = 0.7f;
    params.top_p = 0.8f;
    params.top_k = 4;
    params.seed = 42;
    claw_sampling_session * random_a = claw_sampling_session_create(engine, nullptr, &params);
    claw_sampling_session * random_b = claw_sampling_session_create(engine, nullptr, &params);
    assert(random_a != nullptr && random_b != nullptr);
    const float random_logits[] = {2.0f, 1.5f, 1.0f, 0.5f};
    std::mt19937 reference_rng(42);
    for (int step = 0; step < 16; ++step) {
        claw_sampling_result left{};
        claw_sampling_result right{};
        assert(claw_sampling_session_sample_f32(random_a, random_logits, &left) == 0);
        assert(claw_sampling_session_sample_f32(random_b, random_logits, &right) == 0);
        assert(left.token_id == right.token_id);
        assert(left.token_id == reference_llama_random_sample(
            random_logits, 4, params.top_p, params.temperature, reference_rng));
    }
    claw_sampling_session_destroy(random_a);
    claw_sampling_session_destroy(random_b);

    // llama.cpp top-p with p=0 and min_keep=1 retains the single best token.
    params.top_p = 0.0f;
    params.top_k = 0;
    params.repeat_last_n = 0;
    claw_sampling_session * zero_top_p = claw_sampling_session_create(engine, nullptr, &params);
    assert(zero_top_p != nullptr);
    assert(claw_sampling_session_sample_f32(zero_top_p, random_logits, &result) == 0);
    assert(result.token_id == 0);
    claw_sampling_session_destroy(zero_top_p);

    // Exercise the block-local path across multiple 128-token blocks, its
    // equal-logit earliest-token behavior, and the large-K heap fallback.
    std::vector<std::string> large_pieces;
    std::vector<const char *> large_piece_pointers;
    std::vector<uint32_t> large_piece_lengths;
    large_pieces.reserve(260);
    large_piece_pointers.reserve(260);
    large_piece_lengths.reserve(260);
    for (int index = 0; index < 260; ++index) {
        large_pieces.push_back("token_" + std::to_string(index));
    }
    for (const std::string & piece : large_pieces) {
        large_piece_pointers.push_back(piece.data());
        large_piece_lengths.push_back(static_cast<uint32_t>(piece.size()));
    }
    claw_sampling_engine * large_engine = claw_sampling_engine_create(
        large_pieces.size(),
        large_piece_pointers.data(),
        large_piece_lengths.data(),
        nullptr,
        0);
    assert(large_engine != nullptr);

    params.temperature = 0.0f;
    params.top_p = 1.0f;
    params.top_k = 3;
    params.repeat_penalty = 2.0f;
    params.repeat_last_n = 1;
    claw_sampling_session * block_session =
        claw_sampling_session_create(large_engine, nullptr, &params);
    assert(block_session != nullptr);
    std::vector<float> block_logits(260, -100.0f);
    block_logits[5] = 10.0f;
    block_logits[129] = 9.0f;
    block_logits[259] = 8.0f;
    assert(claw_sampling_session_sample_f32(
        block_session, block_logits.data(), &result) == 0);
    assert(result.token_id == 5);
    assert(claw_sampling_session_sample_f32(
        block_session, block_logits.data(), &result) == 0);
    assert(result.token_id == 129);
    claw_sampling_session_destroy(block_session);

    // Cached FP16 block maxima remain safe when repeat_penalty < 1 raises a
    // repeated logit above the maximum that was observed during Pass 1.
    params.top_k = 1;
    params.repeat_penalty = 0.5f;
    params.repeat_last_n = 1;
    claw_sampling_session * cached_max_session =
        claw_sampling_session_create(large_engine, nullptr, &params);
    assert(cached_max_session != nullptr);
    std::vector<uint16_t> cached_max_logits(260, 0xd640u);  // -100
    cached_max_logits[5] = 0x4500u;                         // 5
    cached_max_logits[200] = 0x4600u;                       // 6
    assert(claw_sampling_session_sample_f16(
        cached_max_session, cached_max_logits.data(), &result) == 0);
    assert(result.token_id == 200);
    cached_max_logits[200] = 0x4400u;  // 4 / 0.5 = 8 after repeat penalty
    assert(claw_sampling_session_sample_f16(
        cached_max_session, cached_max_logits.data(), &result) == 0);
    assert(result.token_id == 200);
    claw_sampling_session_destroy(cached_max_session);

    params.top_k = 1;
    params.repeat_penalty = 1.0f;
    params.repeat_last_n = 0;
    block_logits[5] = 10.0f;
    block_logits[129] = 10.0f;
    claw_sampling_session * tie_session =
        claw_sampling_session_create(large_engine, nullptr, &params);
    assert(tie_session != nullptr);
    assert(claw_sampling_session_sample_f32(
        tie_session, block_logits.data(), &result) == 0);
    assert(result.token_id == 5);
    claw_sampling_session_destroy(tie_session);

    // The maximum-only FP16 block path preserves the same earliest-token tie
    // behavior while blocks are selectively processed in vocabulary order.
    std::vector<uint16_t> block_fp16_logits(260, 0xd640u);  // -100
    block_fp16_logits[5] = 0x4500u;                         // 5
    block_fp16_logits[129] = 0x4500u;                       // 5
    claw_sampling_session * fp16_tie_session =
        claw_sampling_session_create(large_engine, nullptr, &params);
    assert(fp16_tie_session != nullptr);
    assert(claw_sampling_session_sample_f16(
        fp16_tie_session, block_fp16_logits.data(), &result) == 0);
    assert(result.token_id == 5);
    claw_sampling_session_destroy(fp16_tie_session);

    // Generic small K uses the unmasked FP16 block-local path. A repeated
    // winner is kept in FP32 while non-repeat candidates remain FP16 until
    // they pass the block and 16-token threshold screens.
    params.top_k = 3;
    params.repeat_penalty = 2.0f;
    params.repeat_last_n = 1;
    claw_sampling_session * fp16_block_session =
        claw_sampling_session_create(large_engine, nullptr, &params);
    assert(fp16_block_session != nullptr);
    std::fill(block_fp16_logits.begin(), block_fp16_logits.end(), 0xd640u);
    block_fp16_logits[5] = 0x4900u;    // 10
    block_fp16_logits[129] = 0x4880u;  // 9
    block_fp16_logits[259] = 0x4800u;  // 8
    assert(claw_sampling_session_sample_f16(
        fp16_block_session, block_fp16_logits.data(), &result) == 0);
    assert(result.token_id == 5);
    assert(claw_sampling_session_sample_f16(
        fp16_block_session, block_fp16_logits.data(), &result) == 0);
    assert(result.token_id == 129);
    claw_sampling_session_destroy(fp16_block_session);

    params.top_k = 80;
    block_logits[259] = 20.0f;
    claw_sampling_session * fallback_session =
        claw_sampling_session_create(large_engine, nullptr, &params);
    assert(fallback_session != nullptr);
    assert(claw_sampling_session_sample_f32(
        fallback_session, block_logits.data(), &result) == 0);
    assert(result.token_id == 259);
    claw_sampling_session_destroy(fallback_session);

    // Positive top-k values above the old local-heap cutoff use the same
    // unified FP16 block path and retain exact greedy selection.
    block_fp16_logits[259] = 0x4d00u;  // 20
    claw_sampling_session * fp16_large_k_session =
        claw_sampling_session_create(large_engine, nullptr, &params);
    assert(fp16_large_k_session != nullptr);
    assert(claw_sampling_session_sample_f16(
        fp16_large_k_session, block_fp16_logits.data(), &result) == 0);
    assert(result.token_id == 259);
    claw_sampling_session_destroy(fp16_large_k_session);
    claw_sampling_engine_destroy(large_engine);

    params.top_p = 0.0f;
    params.top_k = 0;

    claw_sampling_session * lingering = claw_sampling_session_create(engine, nullptr, &params);
    assert(lingering != nullptr);
    claw_sampling_engine_destroy(engine);
    assert(claw_sampling_session_sample_f32(lingering, random_logits, &result) == 0);
    claw_sampling_session_destroy(lingering);
    return 0;
}
