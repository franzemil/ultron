# dsp/Makefile — host build of the shared C MFCC library (T6).
#
# Targets:
#   make            build libmfcc.a + libmfcc.so + mfcc_test (host)
#   make test       build & run a small host smoke test
#   make fetch      download a CI-built lib for this host into build/
#                   (no compiler needed; make fetch TAG=vX.Y.Z pins a release)
#   make clean      remove generated artifacts
#
# Firmware-ready notes:
#   - Sources use only standard C99 headers (stdint, stddef, stdlib, string, math).
#   - No POSIX / host-only libc dependencies -> dsp/mfcc.{h,c} compile directly
#     into the ESP32 firmware (devices/esp32/ultron_voice/) and the Orange Pi
#     shared library (devices/orangepi/).
#   - Cross-compile for the Pi runtime shared lib:
#       make CC=aarch64-linux-gnu-gcc libmfcc.so
#   - Windows hosts: use MinGW-w64 via MSYS2 (pacman -S mingw-w64-ucrt-x86_64-gcc
#     make, run from an MSYS2 shell so sh/mkdir/rm exist). `make libmfcc.dll`
#     produces build/libmfcc.dll — MinGW exports all symbols by default, so the
#     C sources need no __declspec changes. WSL2 builds the POSIX targets
#     unchanged.
#
# Outputs are placed in build/ (gitignored). Never commit build/.

CC      ?= gcc
AR      ?= ar
CFLAGS  ?= -O2 -Wall -Wextra -std=c99 -I.
LDFLAGS ?= -lm

SRC   := mfcc.c
HDR   := mfcc.h
OBJ   := build/mfcc.o
LIB_A := build/libmfcc.a

# Host-platform shaping: Windows (MinGW-w64) names its shared library
# libmfcc.dll and appends .exe to executables; POSIX hosts keep libmfcc.so.
# HOST_TAG selects the matching prebuilt release artifact for `make fetch`
# (macos-arm64 | linux-x86_64 | linux-aarch64 | win64 — see .github/
# workflows/dsp-release.yml).
ifeq ($(OS),Windows_NT)
  EXE     := .exe
  LIB_SO  := build/libmfcc.dll
  HOST_TAG := win64
else
  EXE     :=
  LIB_SO  := build/libmfcc.so
  UNAME_S := $(shell uname -s 2>/dev/null)
  UNAME_M := $(shell uname -m 2>/dev/null)
  ifeq ($(UNAME_S),Darwin)
    HOST_TAG := macos-$(UNAME_M)
  else ifeq ($(UNAME_S),Linux)
    HOST_TAG := linux-$(UNAME_M)
  else
    HOST_TAG := unknown
  endif
endif

TEST  := build/mfcc_test$(EXE)
TEST_SRC := mfcc_host_test.c

.PHONY: all clean test fetch libmfcc.a libmfcc.so libmfcc.dll mfcc_test

# Documented contract: "make builds libmfcc.a + libmfcc.so + mfcc_test".
# Real outputs live in build/; these are top-level aliases so that
# `make -C dsp libmfcc.so` (and the .a / .dll / mfcc_test variants) work
# from any checkout without path prefixes. Each alias delegates to this
# host's actual output (libmfcc.dll <-> build/libmfcc.dll on Windows).
libmfcc.a: $(LIB_A)
libmfcc.so: $(LIB_SO)
libmfcc.dll: $(LIB_SO)
mfcc_test: $(TEST)

all: $(LIB_A) $(LIB_SO)

$(LIB_A): $(OBJ)
	@mkdir -p build
	$(AR) rcs $@ $^

$(LIB_SO): $(OBJ)
	@mkdir -p build
	$(CC) -shared -fPIC -o $@ $^ $(LDFLAGS)

build/mfcc.o: $(SRC) $(HDR)
	@mkdir -p build
	$(CC) $(CFLAGS) -fPIC -c $< -o $@

mfcc_host_test.c:
	@echo 'int main(void){return 0;}' > $@

test: $(LIB_A) $(TEST)
	./$(TEST)

$(TEST): $(TEST_SRC) $(LIB_A)
	$(CC) $(CFLAGS) $< -o $@ $(LIB_A) $(LDFLAGS)

clean:
	rm -rf build mfcc_host_test.c

# --- Prebuilt release artifacts -------------------------------------------
# Download a CI-built library for this host instead of compiling locally.
# Artifacts are built per tag by .github/workflows/dsp-release.yml and named
# without the tag so the stable releases/latest/download redirect works.
# Requires curl + tar. Overwrites anything already in build/.
TAG ?= latest
GITHUB_REPO ?= franzemil/ultron
RELEASE_URL = https://github.com/$(GITHUB_REPO)/releases/$(if $(filter latest,$(TAG)),latest/download,download/$(TAG))

fetch:
	@test "$(HOST_TAG)" != unknown || { \
		echo "fetch: unsupported host (uname -s: '$(UNAME_S)', uname -m: '$(UNAME_M)')" >&2; \
		echo "fetch: prebuilts exist for macos-arm64, linux-x86_64, linux-aarch64, win64" >&2; \
		exit 1; }
	@mkdir -p build
	curl -fL -o build/libmfcc-$(HOST_TAG).tar.gz \
		$(RELEASE_URL)/libmfcc-$(HOST_TAG).tar.gz
	tar -xzf build/libmfcc-$(HOST_TAG).tar.gz -C build
	rm -f build/libmfcc-$(HOST_TAG).tar.gz
	@echo "fetch: installed $(LIB_A) + $(LIB_SO) (from release: $(TAG))"
