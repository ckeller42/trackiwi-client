# Export formats

`trackiwi export --format <gpx|geojson|csv>` renders cached positions; see the
[CLI reference](cli.md) for the options. The renderers are pure functions in
`trackiwi/export.py` (`to_gpx`, `to_geojson`, `to_csv`) and carry doctests.

| Format | Shape | Several trackers |
|---|---|---|
| `gpx` | GPX 1.1 | One `<trk>` per tracker, named `tracker <id>`. |
| `geojson` | A `FeatureCollection` | One `Feature` per tracker, with `tracker_id` in `properties`. |
| `csv` | A header row, then one row per position in `trackiwi.COLUMNS` order | The `tracker_id` column. |

Without `--tracker`, an export covers every device and keeps them apart. Merging two devices
into one track would draw a route that jumps between them, which looks plausible and cannot be
spotted afterwards.

All timestamps are UTC. Rows that make GPX or GeoJSON impossible (a non-finite coordinate, a
`fix_at` outside the representable range) make the command fail with a clean error instead of
writing a wrong file; `csv` is a raw dump and does not check. See `REQ_EXPORT_PER_TRACKER` and
`REQ_GEOJSON_VALID` in the [requirements](../requirements.rst).

## Writing export files

`export -o <path>` writes atomically, through a temporary file next to the target and a rename:

- A new file is created mode `0600`, because an export is a vehicle's movement history.
  Re-exporting over an existing file keeps whatever mode it already had.
- A symlink destination is followed: the file it points to is updated and the symlink stays.
- A writable directory is required, not only a writable file. Exporting onto a file in a
  read-only directory fails cleanly and leaves the previous export untouched.

A stopped reader (`trackiwi export --format csv | head`) exits `0` for `export`. Every other
command treats a dead output stream as a failure.
