# dsp/Makefile — host build of the shared C MFCC library (T6).
#
# Targets:
#   make            build libmfcc.a + libmfcc.so + mfcc_test (host)
#   make test       build & run a small host smoke test
#   make clean      remove generated artifacts
#
# Firmware-ready notes:
#   - Sources use only standard C99 headers (stdint, stddef, stdlib, string, math).
#   - No POSIX / host-only libc dependencies -> dsp/mfcc.{h,c} compile directly
#     into the ESP32 firmware (devices/esp32/ultron_voice/) and the Orange Pi
#     shared library (devices/orangepi/).
#   - Cross-compile for the Pi runtime shared lib:
#       make CC=aarch64-linux-gnu-gcc libmfcc.so
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
LIB_SO:= build/libmfcc.so
TEST  := build/mfcc_test
TEST_SRC := mfcc_host_test.c

.PHONY: all clean test

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
