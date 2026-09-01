#pragma once

#include <stddef.h>
#include <stdint.h>

#if defined(_WIN32)
#define CLAW_SAMPLING_API __declspec(dllexport)
#else
#define CLAW_SAMPLING_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef struct claw_sampling_engine claw_sampling_engine;
typedef struct claw_sampling_session claw_sampling_session;

typedef struct claw_sampling_params {
    float temperature;
    float top_p;
    int32_t top_k;
    float repeat_penalty;
    float frequency_penalty;
    float presence_penalty;
    int32_t repeat_last_n;
    int32_t newline_token_id;
    int32_t penalize_newline;
    int64_t seed;
} claw_sampling_params;

typedef struct claw_sampling_result {
    int32_t token_id;
    uint8_t mask_applied;
    uint8_t grammar_active_before;
    uint8_t grammar_active_after;
    uint8_t grammar_completed;
    double mask_ms;
    double sampler_ms;
    double accept_ms;
} claw_sampling_result;

/* Model-level owner of TokenizerInfo and the cached XGrammar compiler. */
CLAW_SAMPLING_API claw_sampling_engine * claw_sampling_engine_create(
    size_t vocab_size,
    const char * const * vocab_pieces,
    const uint32_t * vocab_piece_lengths,
    const int32_t * stop_token_ids,
    size_t stop_token_count);

CLAW_SAMPLING_API void claw_sampling_engine_destroy(claw_sampling_engine * engine);

/* Request-level owner of grammar matcher, sampler state, history, and RNG. */
CLAW_SAMPLING_API claw_sampling_session * claw_sampling_session_create(
    claw_sampling_engine * engine,
    const char * structural_tag_json,
    const claw_sampling_params * params);

CLAW_SAMPLING_API void claw_sampling_session_destroy(claw_sampling_session * session);

CLAW_SAMPLING_API int claw_sampling_session_sample_f16(
    claw_sampling_session * session,
    const uint16_t * logits,
    claw_sampling_result * result);

CLAW_SAMPLING_API int claw_sampling_session_sample_f32(
    claw_sampling_session * session,
    const float * logits,
    claw_sampling_result * result);

CLAW_SAMPLING_API const char * claw_sampling_last_error(void);

#ifdef __cplusplus
}
#endif
