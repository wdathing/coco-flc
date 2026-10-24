# flcplay - FLI/FLC player for the TRS-80 Color Computer
#
#   make            build build/FLCPLAY.BIN and the test disks
#   make run        boot XRoar (CoCo 3) with the test disks in drives 0 and 1
#   make clean

CMOC    ?= cmoc
DECB    ?= decb
XROAR   ?= xroar
PYTHON  ?= python3

ORG     := 1000
LIMIT   := 5C00

BUILD   := build
BIN     := $(BUILD)/FLCPLAY.BIN
DISK0   := $(BUILD)/flcplay.dsk
DISK1   := $(BUILD)/flctest2.dsk

SRCS    := src/main.c src/flcdec.asm
TESTS   := test/BALLS.FLC test/SMALL.FLC test/BOUNCE.FLI

all: $(DISK0) $(DISK1)

# Test hooks: make AUTOPLAY=1 AUTOEXIT=2 plays file #1 at start and returns to
# the browser after two loops (the emulator can't press keys for us).
DEFS    :=
ifdef AUTOPLAY
DEFS    += -DAUTOPLAY=$(AUTOPLAY)
endif
ifdef AUTOEXIT
DEFS    += -DAUTOEXIT=$(AUTOEXIT)
endif

$(BIN): $(SRCS) $(BUILD)/defs.txt | $(BUILD)
	$(CMOC) -O2 --coco --org=$(ORG) --limit=$(LIMIT) $(DEFS) -i --intdir=$(BUILD) -o $@ $(SRCS)

# rebuild when the test defines change
$(BUILD)/defs.txt: FORCE | $(BUILD)
	@echo '$(DEFS)' | cmp -s - $@ || echo '$(DEFS)' > $@

$(TESTS) &: tools/mkflc.py tools/flclib.py
	$(PYTHON) tools/mkflc.py test

$(DISK0): $(BIN) test/BALLS.FLC test/SMALL.FLC
	rm -f $@
	$(DECB) dskini $@
	$(DECB) copy -2 -b $(BIN) $@,FLCPLAY.BIN
	$(DECB) copy -2 -b test/BALLS.FLC $@,BALLS.FLC
	$(DECB) copy -2 -b test/SMALL.FLC $@,SMALL.FLC

$(DISK1): $(BIN) test/BOUNCE.FLI
	rm -f $@
	$(DECB) dskini $@
	$(DECB) copy -2 -b $(BIN) $@,FLCPLAY.BIN
	$(DECB) copy -2 -b test/BOUNCE.FLI $@,BOUNCE.FLI

$(BUILD):
	mkdir -p $@

run: all
	$(XROAR) -default-machine coco3 -ram 512 -load-fd0 $(DISK0) -load-fd1 $(DISK1) \
	    -type 'LOADM"FLCPLAY":EXEC\r'

clean:
	rm -rf $(BUILD)

.PHONY: all run clean FORCE
