# Board programs, built on the KV260: make -C sw
CC ?= gcc
CFLAGS ?= -O2 -mcpu=cortex-a53
BUILD ?= ../build/sw

all: $(BUILD)/transcribe $(BUILD)/sttcap

$(BUILD)/transcribe: transcribe.c kv260.c kv260.h
	mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -std=c11 -Wall -Wextra -o $@ transcribe.c kv260.c -lm

$(BUILD)/sttcap: sttcap.c phrase.c phrase.h kv260.c kv260.h display.c display.h
	mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -std=c11 -Wall -Wextra -o $@ sttcap.c phrase.c kv260.c display.c -lm -pthread

# Display layouts on a file in place of the framebuffer
test: $(BUILD)/test_display
	$(BUILD)/test_display

$(BUILD)/test_display: test_display.c display.c display.h
	mkdir -p $(BUILD)
	$(CC) $(CFLAGS) -std=c11 -Wall -Wextra -o $@ test_display.c display.c

clean:
	rm -rf $(BUILD)

.PHONY: all test clean
