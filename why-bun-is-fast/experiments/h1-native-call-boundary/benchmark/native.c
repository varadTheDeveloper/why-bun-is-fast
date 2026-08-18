// H1 FFI shared library — used by Bun's bun:ffi (Bun's normal/documented
// native binding mechanism) and Deno's Deno.dlopen() (Deno's own FFI
// mechanism). Two symbols: a 32-bit variant (eligible for Deno's V8
// Fast-API auto-optimization per Deno's documented FFI behavior) and a
// 64-bit variant (V8 Fast API does not support 64-bit integers directly,
// so this should NOT be fast-path-eligible — used as Deno's deliberate
// non-fast-path variant, verified empirically in the results, not just
// assumed from docs).
#include <stdint.h>

__attribute__((visibility("default")))
int32_t native_increment_i32(int32_t x) {
    return x + 1;
}

__attribute__((visibility("default")))
int64_t native_increment_i64(int64_t x) {
    return x + 1;
}
