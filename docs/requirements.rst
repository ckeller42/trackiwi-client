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

   The only state-changing request the client may issue is
   ``DELETE /api/v2/session`` (logout). No call may alter trackers, alarms,
   tours, markers or shares. (Design spec §7.5.)

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

   The credentials file is created at mode 0600 and a widened mode is narrowed
   back on load. (Design spec §7.3.)

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
