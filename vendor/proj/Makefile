PROJ_VERSION ?= 9.0.0
SHELL = /bin/bash

export PREFIX ?= $(abspath env)

ifeq ($(OS),Windows_NT)
	PLATFORM := Windows
else
	PLATFORM := $(shell uname -s)
endif

PROJ_ARCHIVE := proj-$(PROJ_VERSION).tar.gz

export PKG_CONFIG_PATH := $(realpath $(PREFIX)/lib/pkgconfig)
ifeq ($(PLATFORM),Darwin)
	CCACHE_PATH = /usr/local/opt/ccache/bin
	LIBSUFFIX = dylib
	export PKG_CONFIG_PATH += /usr/local/opt/sqlite/lib/pkgconfig
else ifeq ($(PLATFORM),Linux)
	CCACHE_PATH = /usr/lib/ccache
	CCACHE_PATH := $(or $(CCACHE_PATH),/usr/lib64/ccache)
	LIBSUFFIX = so
endif

# use ccache if available
export PATH := $(CCACHE_PATH):$(PREFIX)/bin:$(PATH)

export PKG_CONFIG_PATH := $(realpath $(PREFIX)/lib/pkgconfig)

build-proj := src/src/.libs/libproj.$(LIBSUFFIX)
configure-proj = build/Makefile

libsqlite := $(PREFIX)/lib/libsqlite3.$(LIBSUFFIX)
libs := $(libsqlite)

# default target
.PHONY: all
all: $(build-proj)

.PHONY: clean
clean:
	-$(RM) -r env
	-cmake --build build/ --target clean

.PHONY: cleaner
cleaner: clean
	-$(RM) -r build/

.PHONY: cleanest
cleanest:
	-$(RM) -r src/

.PHONY: clean-configure
clean-configure:
	-$(RM) $(configure-proj)

#
# Download Archives
#

$(PROJ_ARCHIVE):
	wget https://download.osgeo.org/proj/$@ -O $@

.PHONY: archive
archive: $(PROJ_ARCHIVE)

#
# Extract Archives
#

src: $(PROJ_ARCHIVE)
	rm -rf $@
	mkdir -p $@
	@echo "Extracting $(PROJ_ARCHIVE) ..."
	tar xzf $(PROJ_ARCHIVE) --strip-components=1 -C $@

.PHONY: source
source: src

#
# PROJ
#

.PHONY: configure
configure: clean-configure $(configure-proj)

$(configure-proj): | src $(libs)
	cmake -S src -B build \
		-DCMAKE_INSTALL_PREFIX=$(PREFIX) \
		-DSQLITE3_LIBRARY=$(libsqlite) \
		-DSQLITE3_INCLUDE_DIR=$(PREFIX)/include/ \
		-DENABLE_TIFF=OFF \
		-DENABLE_CURL=OFF \
		-DBUILD_PROJSYNC=OFF \
		-DBUILD_TESTING=OFF

$(build-proj): $(configure-proj)
	cmake \
		--build build \
		--config RelWithDebInfo
	$(MAKE) print-lib-deps-$(PLATFORM)
	$(MAKE) check-lib-deps-$(PLATFORM)

.PHONY: install
install: $(build-proj)
	cmake \
		--install build \
		--config RelWithDebInfo

.PHONY: print-lib-deps
print-lib-deps: print-lib-deps-$(PLATFORM)

.PHONY: print-lib-deps-Darwin
print-lib-deps-Darwin:
	@echo -e "\nExamining library dependencies..."
	find build/ \( -name "*.dylib" -o -name "*.so" \) -exec otool -L {} \;

.PHONY: print-lib-deps-Linux
print-lib-deps-Linux:
	@echo -e "\nExamining library dependencies..."
	find build/ -name "*.so" -print -exec ldd {} \;

.PHONY: check-lib-deps
check-lib-deps: check-lib-deps-$(PLATFORM)

.PHONY: check-lib-deps-Darwin
check-lib-deps-Darwin:
	@echo -e "\nLooking for missing library dependencies..."
	find build \( -name "*.dylib" -o -name "*.so" \) | xargs -t -I % sh -c '! (otool -l % | grep /usr/local/ )'

.PHONY: check-lib-deps-Linux
check-lib-deps-Linux:
	@echo -e "\nLooking for missing library dependencies..."
	find build -name "*.so" | xargs -t -I % sh -c '! ( ldd % | grep -P " => /usr/(local|lib)/(?!x86_64-linux-gnu)" )'
