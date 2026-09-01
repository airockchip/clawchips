#pragma once

#include <stdint.h>

#if defined(_WIN32)
#define CLAW_TOKENIZER_API __declspec(dllexport)
#else
#define CLAW_TOKENIZER_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef struct claw_vocab_info {
    int32_t vocab_size;
    int32_t special_bos_id[64];
    int32_t special_eos_id[64];
    int32_t n_special_bos_id;
    int32_t n_special_eos_id;
    int32_t linefeed_id;
} claw_vocab_info;

CLAW_TOKENIZER_API void * claw_tokenizer_create(const char * tokenizer_path);
CLAW_TOKENIZER_API void claw_tokenizer_destroy(void * handle);
CLAW_TOKENIZER_API const char * claw_tokenizer_last_error(void);
CLAW_TOKENIZER_API int claw_tokenizer_get_vocab_info(void * handle, claw_vocab_info * info);
CLAW_TOKENIZER_API int claw_tokenizer_encode(
    void * handle, const char * text, int32_t text_len, int32_t * tokens, int32_t capacity);
// Returns the required buffer size including the trailing NUL. Writes only when capacity is sufficient.
CLAW_TOKENIZER_API int claw_tokenizer_decode(
    void * handle, const int32_t * tokens, int32_t token_count, char * output, int32_t capacity);
CLAW_TOKENIZER_API int claw_tokenizer_token_to_piece(
    void * handle, int32_t token, char * output, int32_t capacity);
// Reads a string value directly from GGUF metadata. Return convention matches decode().
CLAW_TOKENIZER_API int claw_tokenizer_get_metadata(
    const char * tokenizer_path, const char * key, char * output, int32_t capacity);

#ifdef __cplusplus
}
#endif
