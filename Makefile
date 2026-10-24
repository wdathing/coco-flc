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
TESTS   := test/BALLS.FLC test/SMALL.FLC test/BOUNCE.FLI test/LOOP.FLI
P4TESTS := test/BALLS4.FLI test/LOOP4.FLI

all: $(DISK0) $(DISK1)

# Test hooks: make AUTOPLAY=1 AUTOEXIT=2 plays file #1 at start and returns to
# the browser after two loops (the emulator can't press keys for us).
# NODELAY=1 ignores the file's frame delay to measure raw decode speed.
DEFS    :=
ifdef AUTOPLAY
DEFS    += -DAUTOPLAY=$(AUTOPLAY)
endif
ifdef AUTOEXIT
DEFS    += -DAUTOEXIT=$(AUTOEXIT)
endif
ifdef NODELAY
DEFS    += -DNODELAY
endif

$(BIN): $(SRCS) $(BUILD)/defs.txt | $(BUILD)
	$(CMOC) -O2 --coco --org=$(ORG) --limit=$(LIMIT) $(DEFS) -i --intdir=$(BUILD) -o $@ $(SRCS)

# rebuild when the test defines change
$(BUILD)/defs.txt: FORCE | $(BUILD)
	@echo '$(DEFS)' | cmp -s - $@ || echo '$(DEFS)' > $@

$(TESTS) &: tools/mkflc.py tools/flclib.py
	$(PYTHON) tools/mkflc.py test

# PACK4 (CoCo packed format) versions of two of them
test/%4.FLI: test/%.FLC tools/coco16.py tools/flclib.py
	$(PYTHON) tools/coco16.py --pack4 --verify $< $@
test/%4.FLI: test/%.FLI tools/coco16.py tools/flclib.py
	$(PYTHON) tools/coco16.py --pack4 --verify $< $@

$(DISK0): $(BIN) test/BALLS.FLC test/SMALL.FLC
	rm -f $@
	$(DECB) dskini $@
	$(DECB) copy -2 -b $(BIN) $@,FLCPLAY.BIN
	$(DECB) copy -2 -b test/BALLS.FLC $@,BALLS.FLC
	$(DECB) copy -2 -b test/SMALL.FLC $@,SMALL.FLC

$(DISK1): $(BIN) test/BOUNCE.FLI test/LOOP.FLI $(P4TESTS)
	rm -f $@
	$(DECB) dskini -8 $@
	$(DECB) copy -2 -b $(BIN) $@,FLCPLAY.BIN
	$(DECB) copy -2 -b test/BOUNCE.FLI $@,BOUNCE.FLI
	$(DECB) copy -2 -b test/LOOP.FLI $@,LOOP.FLI
	$(DECB) copy -2 -b test/BALLS4.FLI $@,BALLS4.FLI
	$(DECB) copy -2 -b test/LOOP4.FLI $@,LOOP4.FLI

$(BUILD):
	mkdir -p $@

run: all
	$(XROAR) -default-machine coco3 -ram 512 -load-fd0 $(DISK0) -load-fd1 $(DISK1) \
	    -type 'LOADM"FLCPLAY":EXEC\r'

clean:
	rm -rf $(BUILD)

# --- 3D PC (1993/94) screen saver animations -------------------------------
# media/3dpc/*.3D are the original 256-colour FLIs. `make 3dpc` converts them
# to the CoCo packed format (tools/coco16.py --pack4) and packs them onto
# self-booting 80-track disks build/3dpc/3DPC<n>.dsk; the originals that fit
# in RAM and on a disk go on build/3dpc/ORIG<n>.dsk unconverted.
# The animations aren't ours to distribute, so media/ is not in the repository.
ORIG3D  := $(wildcard media/3dpc/*.3D)
CONV3D  := $(patsubst media/3dpc/%.3D,build/3dpc/conv/%.FLI,$(ORIG3D))

build/3dpc/conv/%.FLI: media/3dpc/%.3D tools/coco16.py tools/flclib.py
	@mkdir -p $(@D)
	$(PYTHON) tools/coco16.py --pack4 $< $@

build/3dpc/orig/%.FLI: media/3dpc/%.3D
	@mkdir -p $(@D)
	cp $< $@

3dpc: $(BIN) $(CONV3D) $(patsubst media/3dpc/%.3D,build/3dpc/orig/%.FLI,$(ORIG3D))
	@test -n "$(ORIG3D)" || { echo "no media/3dpc/*.3D files (not distributed; supply your own)"; exit 1; }
	rm -f build/3dpc/*.dsk
	$(PYTHON) tools/mkdisks.py build/3dpc 3DPC $(BIN) $(CONV3D)
	$(PYTHON) tools/mkdisks.py build/3dpc ORIG $(BIN) build/3dpc/orig/*.FLI

run3dpc: 3dpc
	$(XROAR) -default-machine coco3 -ram 512 -tv-input rgb \
	    -load-fd0 build/3dpc/3DPC1.dsk -load-fd1 build/3dpc/3DPC2.dsk \
	    -type 'LOADM"FLCPLAY":EXEC\r'

.PHONY: all run clean FORCE 3dpc run3dpc
