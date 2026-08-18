// H1 N-API native addon — the SAME compiled binary is loaded by both Node
// (its standard/actual native-call boundary, per Stage 13 Section 3) and
// Bun (via Bun's documented Node-API compatibility layer), giving a
// same-machine-code control comparison in addition to each runtime's own
// "normal" native binding mechanism (bun:ffi for Bun, this N-API addon for
// Node — see README.md for the full equivalence discussion).
//
// The native operation is deliberately trivial: one int32 in, increment,
// one int32 out. No allocation beyond N-API's own required argument/return
// value boxing, no I/O, no loops, no serialization beyond what N-API
// itself requires to move a JS number across the boundary.
#include <node_api.h>

static napi_value NativeIncrement(napi_env env, napi_callback_info info) {
  size_t argc = 1;
  napi_value args[1];
  napi_get_cb_info(env, info, &argc, args, NULL, NULL);

  int32_t x;
  napi_get_value_int32(env, args[0], &x);

  napi_value result;
  napi_create_int32(env, x + 1, &result);
  return result;
}

static napi_value Init(napi_env env, napi_value exports) {
  napi_value fn;
  napi_create_function(env, NULL, 0, NativeIncrement, NULL, &fn);
  napi_set_named_property(env, exports, "nativeIncrement", fn);
  return exports;
}

NAPI_MODULE(NODE_GYP_MODULE_NAME, Init)
