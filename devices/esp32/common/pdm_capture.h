/*
 * devices/esp32/common/pdm_capture.h — forwarding header, kept for compatibility.
 *
 * The shared PDM capture module now lives inside the UltronVoice library
 * (devices/esp32/libraries/UltronVoice/src/pdm_capture.h) because an Arduino library
 * must be self-contained: arduino-cli compiles a library's own src/ only, so a module
 * outside it cannot be linked into a sketch that uses the library.
 *
 * Off-the-shelf sketches (T28's wav_recorder, ad-hoc test sketches) can keep including
 * this path: it is one line and cannot drift, because there is exactly one master file.
 */

#ifndef ULTRON_PDM_CAPTURE_COMMON_FORWARD_H
#define ULTRON_PDM_CAPTURE_COMMON_FORWARD_H

#include "../libraries/UltronVoice/src/pdm_capture.h"

#endif /* ULTRON_PDM_CAPTURE_COMMON_FORWARD_H */
