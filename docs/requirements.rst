Requirements
============

Every project requirement is a ``sphinx-needs`` ``req`` object with a stable ID.
Implementing code references these IDs from its docstrings with the ``:need:``
role; :doc:`traceability` links each one to its verifying test(s). IDs are
stable — do not renumber them.

No requirement text contains a real account value: any number below is a
synthetic example.

Safety & privacy
----------------

.. req:: Package imports only the standard library
   :id: REQ_ZERO_DEPS
   :tags: safety, packaging

   The installed package imports only the Python standard library; it has zero
   runtime dependencies. (Design spec §4.)

.. req:: Read-only by construction
   :id: REQ_READONLY
   :tags: safety

   Against the **trackiwi API**, the only state-changing request the client
   may issue is ``DELETE /api/v2/session`` (logout). No call may alter
   trackers, alarms, tours, markers or shares. (Design spec §7.5; rescoped by
   influx spec §3.2 — writes to the user's own InfluxDB are bounded
   separately by REQ_INFLUX_WRITE_SCOPE.)

.. req:: No private data in the repository
   :id: REQ_NO_PRIVATE_DATA
   :tags: privacy

   No private data — position cache, exported tracks, credentials — is ever
   committed; a pre-commit guard blocks it. (Design spec §7.2.)

.. req:: The token is never logged or printed
   :id: REQ_TOKEN_NEVER_LOGGED
   :tags: privacy, security

   The session token is never logged or printed. Server-controlled text that
   can reach output (an error body a proxy may echo a header into) is redacted
   first. (Design spec §7.3.)

Authentication & transport
---------------------------

.. req:: The API base comes from the login response
   :id: REQ_API_BASE_FROM_LOGIN
   :tags: auth

   The API base is taken from the login response's ``server`` field, never
   hardcoded. (Design spec §3.1, §3.2.)

.. req:: The API base must be https
   :id: REQ_API_BASE_HTTPS
   :tags: auth, security

   The API base must start with ``https://``, wherever it came from (login
   response, ``--api-base``, or the config file); an ``http://`` base would send
   the bearer token in cleartext. (Design spec §7.3.)

.. req:: Redirects are never followed
   :id: REQ_NO_REDIRECTS
   :tags: auth, security

   No request follows an HTTP redirect. urllib copies the ``Authorization``
   header onto a redirected request, to any host and any scheme, so a 3xx is
   an error: never success, and never "no more data".

.. req:: The server command header is ignored
   :id: REQ_IGNORE_APP_COMMAND
   :tags: safety, security

   The ``trackiwi-app-command`` response header is ignored entirely; the client
   never executes a server-delivered command. (Design spec §3.5.)

Sync
----

.. req:: Sync is resumable
   :id: REQ_SYNC_RESUME
   :tags: sync

   Sync is offset-based and resumable: a failure never loses fetched rows and
   never advances the stored offset past an unstored row, so re-running
   continues from the highest id already stored (there is no retry loop).
   (Design spec §8.)

.. req:: The sync offset is exclusive
   :id: REQ_SYNC_OFFSET_EXCLUSIVE
   :tags: sync

   The sync ``offset`` is exclusive: a page requested at a given offset returns
   rows strictly greater than it, with no overlap between consecutive pages, so
   the loop advances by the highest id seen. Confirmed against a live account.
   (Design spec §3.6 item 7, §10.)

.. req:: Sync fails loudly on a stuck page
   :id: REQ_SYNC_FAIL_LOUD
   :tags: sync

   An all-unparseable page, or a page whose highest id does not exceed the
   requested offset, raises rather than silently ending or looping forever.
   (Design spec §8.)

Parsing
-------

.. req:: A malformed row is skipped and counted
   :id: REQ_MALFORMED_SKIP
   :tags: parsing

   A malformed field means skip-and-count the row — never crash, never store
   it. Non-finite coordinates and out-of-range timestamps count as malformed.
   (Design spec §8.)

.. req:: fix_at is normalised to epoch seconds
   :id: REQ_FIX_AT_SECONDS
   :tags: parsing

   ``fix_at`` is normalised to epoch seconds: the sync CSV's integer form
   (seconds or milliseconds) and the tracker endpoint's ISO 8601 string form
   both resolve to seconds; a zone-less ISO value is read as UTC. (Design spec
   §3.6 item 4.)

Local cache & config
--------------------

.. req:: The token file is 0600 and self-heals
   :id: REQ_CONFIG_MODE_0600
   :tags: security, storage

   The credentials file and the InfluxDB target config (``influx.toml``) are
   kept at mode 0600, and a widened mode is narrowed back on load.
   (Design spec §7.3; influx spec §5.6.)

.. req:: The position cache is 0600 in a 0700 directory
   :id: REQ_CACHE_MODE_0600
   :tags: security, storage

   The position cache is created at mode 0600 inside a 0700 directory, and a
   widened mode self-heals on open. (Design spec §7.1.)

.. req:: Purge deletes the cache even when corrupt
   :id: REQ_PURGE_DELETES
   :tags: storage, privacy

   ``purge`` deletes the cache and its SQLite sidecar files without opening the
   database, so it still works when the file is corrupt. (Design spec §6, §7.1.)

.. req:: A lock is never mistaken for corruption
   :id: REQ_PURGE_NOT_ON_LOCK
   :tags: storage

   A locked/busy cache is distinguished from a corrupt one; the destructive
   ``purge --yes`` advice is never given for a mere lock. (Design spec §7.1.)

Export
------

.. req:: Export writes atomically
   :id: REQ_EXPORT_ATOMIC
   :tags: export

   ``export -o`` writes atomically (temp file plus ``os.replace``), preserves an
   existing file's mode, follows symlinks, and defaults a new file to 0600.
   (Design spec §6.)

.. req:: A multi-tracker export keeps trackers separate
   :id: REQ_EXPORT_PER_TRACKER
   :tags: export

   An export covering several trackers keeps them apart: one GPX ``<trk>`` and
   one GeoJSON ``Feature`` per tracker, each carrying its ``tracker_id``.
   (Design spec §6.)

.. req:: GeoJSON is RFC 7946-valid
   :id: REQ_GEOJSON_VALID
   :tags: export

   GeoJSON output is RFC 7946-valid: a ``Point`` for a single fix, a
   ``LineString`` for two or more, and never ``Infinity``/``NaN``. (Design spec
   §6.)

.. req:: logout revokes server-side and reports honestly
   :id: REQ_LOGOUT_REVOKES
   :tags: auth, security

   ``logout`` revokes the session server-side (``DELETE /api/v2/session``) and,
   when it cannot, still removes the local credentials and warns that the token
   may remain valid. (Design spec §7.3.)

.. req:: A stored token is always removable
   :id: REQ_TOKEN_REMOVABLE
   :tags: auth, security

   ``logout`` removes the local credentials whatever the config file holds: a
   file that cannot be loaded (truncated JSON, a non-object document, a
   non-string or non-https ``api_base``) is deleted all the same, with a
   warning that the token may remain valid. (Design spec §7.3.)

.. req:: Positions are written to InfluxDB in natural units
   :id: REQ_LINEPROTOCOL_UNITS
   :tags: influx, data

   Each cached row becomes one ``trackiwi_position`` line-protocol point with
   tags ``tracker_id`` and ``tracker_name`` and fields in natural units
   (distance in metres, voltage in volts). NULL optional columns are omitted,
   tag values are escaped, and non-finite values are rejected. (Influx spec §4.)

Heading
-------

.. req:: A heading is estimated from cached positions, read-only
   :id: REQ_HEADING_ESTIMATE
   :tags: heading, safety

   The heading helper is pure arithmetic over cached positions: no request is
   made and the cache is never created by it. Moving (``speed > 0``): the
   heading is the fix's ``course``. Parked: the heading is the initial
   great-circle bearing between the last two moving fixes of the *same*
   tracker, in ``[0, 360)`` and correct across the antimeridian; only when
   that bearing is undefined (one moving fix, or two at the same spot) the
   last moving fix's raw ``course`` is used. No moving fix at all yields
   ``unknown``, never an error, and a NULL or non-finite cached field never
   raises. The caveats — a vehicle that reversed into its spot points the
   opposite way, and GPS alone cannot sense a stationary heading — are stated
   in the library docstring and the command's ``--help``. (Issue #3.)

.. req:: The heading carries a confidence state with a configurable threshold
   :id: REQ_HEADING_STATE
   :tags: heading

   Every estimate carries a state: ``moving``, ``freshly_parked`` (last
   movement no more than ``stale_after`` seconds ago), ``stale`` (older) or
   ``unknown``. ``stale_after`` defaults to the documented
   ``DEFAULT_STALE_AFTER`` (3600 s) and is a parameter of the library
   function and a flag (``--stale-after``) of the command. (Issue #3.)

Mirror
------

.. req:: The mirror never advances past an unacknowledged row
   :id: REQ_MIRROR_RESUME
   :tags: integrity, influx

   The per-target mirror position advances only after InfluxDB acknowledges a
   batch. A failure leaves earlier batches recorded and the failed batch and
   everything after it for the next run. (Influx spec §5.2.)

.. req:: InfluxDB writes are bounded to one measurement and one target
   :id: REQ_INFLUX_WRITE_SCOPE
   :tags: safety, influx

   The only writes to InfluxDB are POSTs of ``trackiwi_position`` line
   protocol to the configured target's write endpoint (``/api/v2/write`` or
   ``/write``). No deletes, no schema or bucket management. (Influx spec §3.2.)

.. req:: InfluxDB credentials are never shown
   :id: REQ_INFLUX_TOKEN_REDACT
   :tags: privacy, security, influx

   The InfluxDB token and password never appear in output: every message
   built from server-controlled text is redacted first. (Influx spec §5.6.)

.. req:: Re-sending positions never creates duplicates
   :id: REQ_MIRROR_IDEMPOTENT
   :tags: integrity, influx

   A cached row always becomes the same point — same measurement, tags and
   timestamp — so re-sending (including a full backfill) overwrites rather
   than duplicates. (Influx spec §4.)

.. req:: Committed deployment templates carry no deployment's specifics
   :id: REQ_PORTABLE_CONFIG
   :tags: privacy, portability, influx

   No file under ``deploy/`` or ``examples/`` contains a hostname, IP
   address, credential, tracker id or coordinate from any real deployment;
   values are placeholders, and every variable the compose file uses is
   defined in ``deploy/example.env``. (Influx spec §6.)

.. req:: The compose stack exposes as little as possible
   :id: REQ_DEPLOY_LEAST_EXPOSURE
   :tags: security, influx

   Every port ``deploy/docker-compose.yml`` publishes binds to ``127.0.0.1``
   unless a bind-address variable is set deliberately, and the ingest
   container receives a token scoped to read and write on the trackiwi bucket
   only — never the operator token. Read is required because ``influx check``
   confirms the bucket exists through ``GET /api/v2/buckets``, which lists only
   the buckets the token may read. (Influx spec §7.1.)

.. req:: The dashboard is importable anywhere
   :id: REQ_DASHBOARD_PORTABLE
   :tags: portability, influx

   The committed Grafana dashboard references its datasource only through a
   ``DS_TRACKIWI`` variable, reads its bucket and tracker from dashboard
   variables, uses Flux throughout, and contains no hardcoded datasource UID
   or real value. (Influx spec §8.)
