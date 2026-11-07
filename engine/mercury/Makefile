# E7 (#66): build the Mercury decision engine.
#
#   make                 builds $(BUILD_DIR)/mercury_engine (default build/mercury_engine)
#   make BUILD_DIR=/tmp/x
#   make clean
#
# Needs mmc (Mercury 22.01.8, hlc.gc grade) on PATH and MERCURY_JSON_SRC pointing at
# mercury_json's src/ directory (.github/actions/install-mercury sets both).
# Sources are copied into BUILD_DIR so mmc's Mercury/ output never lands next to them.
# --halt-at-warn makes any warning (unused import, missing switch arm, ...) a build error.

MMC ?= mmc
GRADE ?= hlc.gc
BUILD_DIR ?= build
SOURCES := rules.m json_io.m mercury_engine.m
MCFLAGS ?= --grade $(GRADE) --halt-at-warn

.PHONY: all clean

all: $(BUILD_DIR)/mercury_engine

$(BUILD_DIR)/mercury_engine: $(SOURCES)
	@test -n "$(MERCURY_JSON_SRC)" || { echo "MERCURY_JSON_SRC is not set" >&2; exit 1; }
	mkdir -p $(BUILD_DIR)
	cp $(SOURCES) $(MERCURY_JSON_SRC)/*.m $(BUILD_DIR)/
	cd $(BUILD_DIR) && $(MMC) -f *.m && $(MMC) $(MCFLAGS) --make mercury_engine

clean:
	rm -rf $(BUILD_DIR)
