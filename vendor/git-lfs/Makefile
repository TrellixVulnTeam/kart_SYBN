# v3.2.0 + some later changes
GIT_LFS_REF ?= c6497f8b1fe78b5cea7468581ef0fde3ca8056f9
GIT_LFS_REPO ?= git-lfs/git-lfs
GIT_LFS_ARCHIVE := git-lfs-$(GIT_LFS_REF).tar.gz

SHELL = /bin/bash
export PREFIX ?= $(abspath env)

ifeq ($(OS),Windows_NT)
	PLATFORM := Windows
else
	PLATFORM := $(shell uname -s)
endif

ifeq ($(PLATFORM),Darwin)
	LIBSUFFIX = dylib
	CURL_PREFIX=/usr/local/opt/curl
	CCACHE_PATH = /usr/local/opt/ccache/bin
	LOADER_PATH = @loader_path
else ifeq ($(PLATFORM),Linux)
	LIBSUFFIX = so
	CURL_PREFIX = $(PREFIX)
	CCACHE_PATH = /usr/lib/ccache
	CCACHE_PATH := $(or $(CCACHE_PATH),/usr/lib64/ccache)
	LOADER_PATH := $$ORIGIN
endif

# default target
.PHONY: all
all: src/bin/git-lfs

.PHONY: clean
clean:
	-$(RM) -r env
	-$(RM) -r src/git

.PHONY: cleaner
cleaner: clean
	-$(MAKE) -C src/ clean

.PHONY: cleanest
cleanest:
	-$(RM) -r src/

#
# Download Archives
#
$(GIT_LFS_ARCHIVE):
	wget https://github.com/$(GIT_LFS_REPO)/archive/$(GIT_LFS_REF).tar.gz -O $@

.PHONY: archive
archive: $(GIT_LFS_ARCHIVE)

#
# Extract Archives
#

src: $(GIT_LFS_ARCHIVE)
	rm -rf $@
	mkdir -p $@
	@echo "Extracting $(GIT_LFS_ARCHIVE) ..."
	tar xzf $(GIT_LFS_ARCHIVE) --strip-components=1 -C $@


.PHONY: source
source: src

src/bin/git-lfs: | src
	$(MAKE) -C src -j 2 $(CONFIG_FLAGS)

#
# INSTALLATION
#

.PHONY: install
install: src/bin/git-lfs
	mkdir -p $(PREFIX)/bin/
	cp -vaf src/bin/git-lfs $(PREFIX)/bin/
