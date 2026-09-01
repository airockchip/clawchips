#include "tokenizer_bridge.h"

#include "Tokenizer.h"

#include <algorithm>
#include <cstring>
#include <exception>
#include <memory>
#include <string>

// The tokenizer SDK's merged static archive exports the GGUF C API, but its
// installed package only guarantees Tokenizer.h. Keep the small stable ABI
// declarations here so the bridge can be built directly on a deployment board.
extern "C" {
struct gguf_context;
struct ggml_context;
struct gguf_init_params {
    bool no_alloc;
    ggml_context ** ctx;
};
enum gguf_type {
    GGUF_TYPE_UINT8 = 0,
    GGUF_TYPE_INT8,
    GGUF_TYPE_UINT16,
    GGUF_TYPE_INT16,
    GGUF_TYPE_UINT32,
    GGUF_TYPE_INT32,
    GGUF_TYPE_FLOAT32,
    GGUF_TYPE_BOOL,
    GGUF_TYPE_STRING,
};
gguf_context * gguf_init_from_file(const char * fname, gguf_init_params params);
void gguf_free(gguf_context * ctx);
int64_t gguf_find_key(const gguf_context * ctx, const char * key);
gguf_type gguf_get_kv_type(const gguf_context * ctx, int64_t key_id);
const char * gguf_get_val_str(const gguf_context * ctx, int64_t key_id);
}

namespace {
thread_local std::string last_error;

void set_error(const std::string & value) {
    last_error = value;
}

int copy_string(const std::string & value, char * output, int32_t capacity) {
    const size_t required = value.size() + 1;
    if (required > static_cast<size_t>(INT32_MAX)) {
        set_error("string result is too large");
        return -1;
    }
    if (output != nullptr && capacity >= static_cast<int32_t>(required)) {
        std::memcpy(output, value.data(), value.size());
        output[value.size()] = '\0';
    }
    return static_cast<int>(required);
}

Tokenizer * as_tokenizer(void * handle) {
    if (handle == nullptr) {
        set_error("tokenizer handle is null");
        return nullptr;
    }
    return static_cast<Tokenizer *>(handle);
}
} // namespace

extern "C" {

void * claw_tokenizer_create(const char * tokenizer_path) {
    last_error.clear();
    if (tokenizer_path == nullptr || tokenizer_path[0] == '\0') {
        set_error("tokenizer path is empty");
        return nullptr;
    }
    try {
        std::unique_ptr<Tokenizer> tokenizer(new Tokenizer(TOKENIZER_BACKEND_LLAMA, tokenizer_path));
        VocabInfo info = {};
        if (!tokenizer->GetVocabInfo(&info)) {
            set_error(std::string("failed to load tokenizer GGUF: ") + tokenizer_path);
            return nullptr;
        }
        return tokenizer.release();
    } catch (const std::exception & exc) {
        set_error(exc.what());
        return nullptr;
    } catch (...) {
        set_error("unknown error while creating tokenizer");
        return nullptr;
    }
}

void claw_tokenizer_destroy(void * handle) {
    delete static_cast<Tokenizer *>(handle);
}

const char * claw_tokenizer_last_error(void) {
    return last_error.c_str();
}

int claw_tokenizer_get_vocab_info(void * handle, claw_vocab_info * output) {
    last_error.clear();
    Tokenizer * tokenizer = as_tokenizer(handle);
    if (tokenizer == nullptr || output == nullptr) {
        if (output == nullptr) set_error("vocab info output is null");
        return -1;
    }
    VocabInfo info = {};
    if (!tokenizer->GetVocabInfo(&info)) {
        set_error("failed to query tokenizer vocabulary");
        return -1;
    }
    output->vocab_size = info.vocab_size;
    output->n_special_bos_id = std::min(info.n_special_bos_id, 64);
    output->n_special_eos_id = std::min(info.n_special_eos_id, 64);
    output->linefeed_id = info.linefeed_id;
    std::copy(info.special_bos_id, info.special_bos_id + output->n_special_bos_id, output->special_bos_id);
    std::copy(info.special_eos_id, info.special_eos_id + output->n_special_eos_id, output->special_eos_id);
    return 0;
}

int claw_tokenizer_encode(
        void * handle, const char * text, int32_t text_len, int32_t * tokens, int32_t capacity) {
    last_error.clear();
    Tokenizer * tokenizer = as_tokenizer(handle);
    if (tokenizer == nullptr || text == nullptr || tokens == nullptr || text_len < 0 || capacity <= 0) {
        if (last_error.empty()) set_error("invalid encode arguments");
        return -1;
    }
    const int result = tokenizer->Tokenize(text, text_len, tokens, capacity);
    if (result < 0) set_error("tokenization failed");
    return result;
}

int claw_tokenizer_decode(
        void * handle, const int32_t * tokens, int32_t token_count, char * output, int32_t capacity) {
    last_error.clear();
    Tokenizer * tokenizer = as_tokenizer(handle);
    if (tokenizer == nullptr || tokens == nullptr || token_count < 0 || capacity < 0) {
        if (last_error.empty()) set_error("invalid decode arguments");
        return -1;
    }
    try {
        return copy_string(tokenizer->Decode(const_cast<int32_t *>(tokens), token_count), output, capacity);
    } catch (const std::exception & exc) {
        set_error(exc.what());
        return -1;
    }
}

int claw_tokenizer_token_to_piece(
        void * handle, int32_t token, char * output, int32_t capacity) {
    last_error.clear();
    Tokenizer * tokenizer = as_tokenizer(handle);
    if (tokenizer == nullptr || capacity < 0) return -1;
    try {
        return copy_string(tokenizer->TokenToPiece(token), output, capacity);
    } catch (const std::exception & exc) {
        set_error(exc.what());
        return -1;
    }
}

int claw_tokenizer_get_metadata(
        const char * tokenizer_path, const char * key, char * output, int32_t capacity) {
    last_error.clear();
    if (tokenizer_path == nullptr || key == nullptr || capacity < 0) {
        set_error("invalid metadata arguments");
        return -1;
    }
    gguf_init_params params = {};
    params.no_alloc = true;
    gguf_context * context = gguf_init_from_file(tokenizer_path, params);
    if (context == nullptr) {
        set_error(std::string("failed to open tokenizer GGUF: ") + tokenizer_path);
        return -1;
    }
    const int64_t key_id = gguf_find_key(context, key);
    if (key_id < 0) {
        gguf_free(context);
        return 0;
    }
    if (gguf_get_kv_type(context, key_id) != GGUF_TYPE_STRING) {
        gguf_free(context);
        set_error(std::string("GGUF metadata is not a string: ") + key);
        return -1;
    }
    const char * value = gguf_get_val_str(context, key_id);
    const int result = copy_string(value == nullptr ? std::string() : std::string(value), output, capacity);
    gguf_free(context);
    return result;
}

} // extern "C"
