# Changelog

All notable changes to this project are documented here.
Format loosely follows Keep a Changelog; versions are `MAJOR.MINOR.PATCH.MICRO`.

## [0.1.0.0] - 2026-05-18

First versioned release of this fork. Reworks how converted books render on
Kobo so chapters open fast and read correctly, and hardens the converter
against untrusted ebooks.

### Added
- `--target kobo|generic` (default `kobo`). `generic` produces a plain
  bionic `.epub` (no kepubify) for Kindle/Boox/Apple Books. `main.py` asks
  once whether you mainly read on a Kobo and remembers the answer in
  `~/.config/calibre_bionic_converter/config.json`. (#19)
- Input caching: re-running a conversion on an unchanged book with the same
  tools/settings now skips instantly instead of re-converting. (#20)
- Pinned, reproducible dependencies. (#26)

### Changed
- Kobo `.epub` conversion now runs kepubify *before* the bionic transform.
  A chapter is a single light document instead of being split into ~10
  fragments, so: chapter entry is fast (was a 10–30s freeze), the chapter
  illustration stays with its number, the table of contents lands on the
  right chapter, "time left in chapter" counts down correctly, and forced
  page breaks fall at the book's own scene breaks instead of mid-sentence.
  (#18, #28, #27)
- Contractions and possessives (`don't`, `Brayan's`) are treated as one
  word, so bionic bolds them once instead of splitting them. (#29)
- calibre's injected oversized drop cap (which clipped on the left when
  zoomed) is removed, matching the source book.

### Fixed
- Argument injection via flag-shaped input paths. (#22, CWE-88)
- Zip-slip, symlink-entry, and decompression-bomb defenses on extraction.
  (#23, #25, CWE-22/409)
- Output written atomically; never follows a symlinked output path. (#24)
- ReDoS in the stylesheet cleaner on hostile CSS. (CWE-1333)
- Loud, clear failures when calibre/kepubify are missing or error, instead
  of a raw traceback or a silently broken book.

### Removed
- The hand "chunker" and its CSS/flow-size machinery, obsoleted by the
  kepub-first pipeline.
