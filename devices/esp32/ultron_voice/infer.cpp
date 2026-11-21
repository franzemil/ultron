/* infer — implementation. See infer.h. */

#include "infer.h"

#include <Arduino.h>
#include <string.h>

#include <Chirale_TensorFlowLite.h>

#include "tensorflow/lite/micro/all_ops_resolver.h"
#include "tensorflow/lite/micro/micro_interpreter.h"
#include "tensorflow/lite/schema/schema_generated.h"

#include "model_data.h" /* generated: g_model[] + ULTRON_MODEL_PLACEHOLDER */

namespace ultron {
namespace {

/* Static arena — NFR-002 budget 150 KB, 16-byte aligned for CMSIS kernels. */
alignas(16) uint8_t g_arena[kTensorArenaBytes];

tflite::AllOpsResolver g_resolver;
const tflite::Model *g_model = nullptr;
tflite::MicroInterpreter *g_interpreter = nullptr;
TfLiteTensor *g_input = nullptr;
TfLiteTensor *g_output = nullptr;
uint32_t g_last_latency_us = 0;
ModelStats g_stats;

}  // namespace

int infer_begin(ModelStats *stats) {
  g_model = tflite::GetModel(g_model_data);
  if (g_model->version() != TFLITE_SCHEMA_VERSION) return 1;

  /* Static interpreter instance: no heap, no `new` (the example's `new` is
   * replaced by a function-local static — same object lifetime, no allocator). */
  static tflite::MicroInterpreter interpreter(g_model, g_resolver, g_arena, sizeof(g_arena));
  g_interpreter = &interpreter;

  if (g_interpreter->AllocateTensors() != kTfLiteOk) return 2;

  g_input = g_interpreter->input(0);
  g_output = g_interpreter->output(0);
  if (g_input == nullptr || g_output == nullptr) return 1;

  if (g_input->dims->size != 3 || g_input->dims->data[0] != 1 || g_input->type != kTfLiteInt8) {
    return 3;
  }
  const int d1 = g_input->dims->data[1];
  const int d2 = g_input->dims->data[2];
  const bool nwc = (d1 == ULTRON_MODEL_FRAMES && d2 == ULTRON_MODEL_COEFFS);
  const bool ncw = (d1 == ULTRON_MODEL_COEFFS && d2 == ULTRON_MODEL_FRAMES);
  if (!nwc && !ncw) return 3;
  if (g_output->dims->size != 2 || g_output->type != kTfLiteInt8) return 4;

  memset(&g_stats, 0, sizeof(g_stats));
  g_stats.model_bytes = g_model_data_len;
  g_stats.arena_bytes = sizeof(g_arena);
  g_stats.arena_used = g_interpreter->arena_used_bytes();
  g_stats.n_classes = g_output->dims->data[1];
  g_stats.n_frames = ULTRON_MODEL_FRAMES;
  g_stats.n_coeffs = ULTRON_MODEL_COEFFS;
  g_stats.input_nwc = nwc;
  g_stats.input_scale = g_input->params.scale;
  g_stats.input_zero_point = g_input->params.zero_point;
  g_stats.output_scale = g_output->params.scale;
  g_stats.output_zero_point = g_output->params.zero_point;
  g_stats.placeholder = ULTRON_MODEL_PLACEHOLDER != 0;
  g_stats.provenance = ULTRON_MODEL_PROVENANCE;

  if (stats != nullptr) *stats = g_stats;
  return 0;
}

int infer_run(const int8_t *features, float *scores, int n_classes) {
  if (g_interpreter == nullptr || features == nullptr || scores == nullptr) return 1;
  if (n_classes != g_stats.n_classes) return 2;

  if (g_stats.input_nwc) {
    memcpy(g_input->data.int8, features,
           (size_t)g_stats.n_frames * (size_t)g_stats.n_coeffs * sizeof(int8_t));
  } else {
    /* NWC features (frame-major) → NCW tensor (coefficient-major). */
    int8_t *dst = g_input->data.int8;
    for (int f = 0; f < g_stats.n_frames; f++) {
      for (int c = 0; c < g_stats.n_coeffs; c++) {
        dst[(size_t)c * (size_t)g_stats.n_frames + (size_t)f] =
            features[(size_t)f * (size_t)g_stats.n_coeffs + (size_t)c];
      }
    }
  }

  const uint32_t t0 = micros();
  if (g_interpreter->Invoke() != kTfLiteOk) return 3;
  g_last_latency_us = micros() - t0;

  /* The graph already ends in softmax; dequantize int8 → probability. */
  for (int i = 0; i < n_classes; i++) {
    scores[i] = ((float)g_output->data.int8[i] - (float)g_stats.output_zero_point) *
                g_stats.output_scale;
  }
  return 0;
}

uint32_t infer_lastLatencyUs() { return g_last_latency_us; }

}  // namespace ultron
