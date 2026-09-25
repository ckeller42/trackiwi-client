Traceability
============

Each requirement is verified by one or more tests. Granularity is
per-requirement: a single ``test`` need points at the real verifying test
node id(s) — the exact string ``pytest`` collects — rather than transcribing
every test function. If a ``req`` below had no incoming ``verifies`` link, the
``req_without_test`` rule in ``conf.py`` would raise a warning and
``sphinx-build -W`` would fail the build.

Verifying tests
---------------

.. test:: Package imports only the standard library
   :id: TEST_ZERO_DEPS
   :verifies: REQ_ZERO_DEPS

   ``tests/test_invariants.py::test_package_imports_only_stdlib``

.. test:: Only the session DELETE changes state
   :id: TEST_READONLY
   :verifies: REQ_READONLY

   ``tests/test_invariants.py::test_logout_uses_only_the_session_delete``

.. test:: The private-data guard blocks committed secrets
   :id: TEST_NO_PRIVATE_DATA
   :verifies: REQ_NO_PRIVATE_DATA

   ``tests/test_check_no_private_data.py::test_database_is_blocked``,
   ``tests/test_check_no_private_data.py::test_token_shaped_string_is_blocked``

.. test:: A token echoed in an error body is redacted
   :id: TEST_TOKEN_NEVER_LOGGED
   :verifies: REQ_TOKEN_NEVER_LOGGED

   ``tests/test_client_sync.py::test_the_sessions_own_token_echoed_in_an_error_body_is_redacted``,
   ``tests/test_client_sync.py::test_bearer_header_echoed_in_an_error_body_is_redacted``

.. test:: Login stores the server-supplied API base
   :id: TEST_API_BASE_FROM_LOGIN
   :verifies: REQ_API_BASE_FROM_LOGIN

   ``tests/test_client_auth.py::test_login_stores_server_token_and_user``

.. test:: A non-https API base is rejected
   :id: TEST_API_BASE_HTTPS
   :verifies: REQ_API_BASE_HTTPS

   ``tests/test_client_auth.py::test_a_plain_http_api_base_is_rejected``,
   ``tests/test_client_auth.py::test_a_saved_config_with_an_http_api_base_is_rejected``

.. test:: The app-command response header is ignored
   :id: TEST_IGNORE_APP_COMMAND
   :verifies: REQ_IGNORE_APP_COMMAND

   ``tests/test_invariants.py::test_app_command_response_header_is_ignored``

.. test:: Sync resumes from the stored offset
   :id: TEST_SYNC_RESUME
   :verifies: REQ_SYNC_RESUME

   ``tests/test_client_sync.py::test_resuming_from_an_offset_skips_initial_sync``,
   ``tests/test_client_sync.py::test_sync_pages_until_empty``

.. test:: The next offset is the highest id seen (exclusive)
   :id: TEST_SYNC_OFFSET_EXCLUSIVE
   :verifies: REQ_SYNC_OFFSET_EXCLUSIVE

   ``tests/test_client_sync.py::test_first_call_requests_initial_sync_then_offsets``

.. test:: A stuck or unparseable page raises
   :id: TEST_SYNC_FAIL_LOUD
   :verifies: REQ_SYNC_FAIL_LOUD

   ``tests/test_client_sync.py::test_all_malformed_page_raises_instead_of_stopping_silently``,
   ``tests/test_client_sync.py::test_non_advancing_offset_raises_instead_of_looping_forever``

.. test:: A malformed row is skipped and counted
   :id: TEST_MALFORMED_SKIP
   :verifies: REQ_MALFORMED_SKIP

   ``tests/test_parse.py::test_short_row_is_skipped_not_fatal``,
   ``tests/test_parse.py::test_non_finite_speed_is_skipped``

.. test:: fix_at is normalised to epoch seconds on both endpoints
   :id: TEST_FIX_AT_SECONDS
   :verifies: REQ_FIX_AT_SECONDS

   ``tests/test_parse.py::test_fix_at_is_normalised_during_parse``,
   ``tests/test_parse.py::test_epoch_from_iso_treats_a_naive_timestamp_as_utc``

.. test:: The config file is 0600 and self-heals
   :id: TEST_CONFIG_MODE_0600
   :verifies: REQ_CONFIG_MODE_0600

   ``tests/test_client_auth.py::test_config_is_created_owner_only_without_relying_on_chmod``,
   ``tests/test_client_auth.py::test_load_self_heals_a_widened_config``,
   ``tests/test_influx_config.py::test_load_narrows_a_widened_config_file``

.. test:: The cache is 0600 in a 0700 directory and self-heals
   :id: TEST_CACHE_MODE_0600
   :verifies: REQ_CACHE_MODE_0600

   ``tests/test_store.py::test_database_is_created_owner_only_without_relying_on_chmod``,
   ``tests/test_store.py::test_permissions_are_self_healed``

.. test:: Purge deletes the cache and sidecars, even when corrupt
   :id: TEST_PURGE_DELETES
   :verifies: REQ_PURGE_DELETES

   ``tests/test_store.py::test_purge_removes_the_sidecar_files``,
   ``tests/test_cli.py::test_purge_deletes_a_corrupt_cache``

.. test:: A locked cache is not reported as corrupt
   :id: TEST_PURGE_NOT_ON_LOCK
   :verifies: REQ_PURGE_NOT_ON_LOCK

   ``tests/test_store.py::test_a_locked_database_is_not_reported_as_corrupt``,
   ``tests/test_cli.py::test_export_with_a_locked_cache_does_not_advise_purge``

.. test:: Export writes atomically and preserves mode
   :id: TEST_EXPORT_ATOMIC
   :verifies: REQ_EXPORT_ATOMIC

   ``tests/test_cli.py::test_export_to_existing_file_preserves_mode``,
   ``tests/test_cli.py::test_export_to_new_file_creates_mode_0600``,
   ``tests/test_cli.py::test_export_to_symlink_follows_and_preserves_symlink``

.. test:: A multi-tracker export keeps trackers separate
   :id: TEST_EXPORT_PER_TRACKER
   :verifies: REQ_EXPORT_PER_TRACKER

   ``tests/test_export.py::test_gpx_emits_one_track_per_tracker``,
   ``tests/test_export.py::test_geojson_emits_one_feature_per_tracker``

.. test:: GeoJSON geometry and finiteness are valid
   :id: TEST_GEOJSON_VALID
   :verifies: REQ_GEOJSON_VALID

   ``tests/test_export.py::test_geojson_geometry_type_is_decided_per_tracker``,
   ``tests/test_export.py::test_geojson_refuses_to_emit_non_finite_coordinates``

.. test:: logout revokes then clears, honestly on failure
   :id: TEST_LOGOUT_REVOKES
   :verifies: REQ_LOGOUT_REVOKES

   ``tests/test_client_auth.py::test_logout_revokes_then_deletes_config``,
   ``tests/test_client_auth.py::test_logout_clears_local_state_even_if_revoke_fails``

.. test:: Line protocol uses natural units and escapes tags
   :id: TEST_LINEPROTOCOL_UNITS
   :verifies: REQ_LINEPROTOCOL_UNITS

   ``tests/test_lineprotocol.py::test_full_row_formats_with_natural_units``,
   ``tests/test_lineprotocol.py::test_tag_value_escapes_space_comma_equals``,
   ``tests/test_lineprotocol.py::test_null_optional_columns_are_omitted``,
   ``tests/test_lineprotocol.py::test_non_finite_value_is_rejected``

.. test:: Mirror position is tracked per target and persists
   :id: TEST_MIRROR_RESUME
   :verifies: REQ_MIRROR_RESUME

   ``tests/test_store_mirror.py::test_mirror_position_defaults_to_zero``,
   ``tests/test_store_mirror.py::test_mirror_position_is_per_target_and_persists``,
   ``tests/test_store_mirror.py::test_existing_cache_gains_the_new_tables``,
   ``tests/test_influx_mirror.py::test_failed_batch_does_not_advance_state``,
   ``tests/test_influx_mirror.py::test_only_new_rows_are_sent_after_more_sync``

.. test:: Only the write endpoint receives POSTs
   :id: TEST_INFLUX_WRITE_SCOPE
   :verifies: REQ_INFLUX_WRITE_SCOPE

   ``tests/test_influx_writer.py::test_only_write_endpoints_receive_posts``,
   ``tests/test_influx_writer.py::test_v2_write_request_is_exact``

.. test:: InfluxDB credentials are redacted from errors
   :id: TEST_INFLUX_TOKEN_REDACT
   :verifies: REQ_INFLUX_TOKEN_REDACT

   ``tests/test_influx_writer.py::test_token_and_password_are_redacted_from_error_bodies``

.. test:: Re-sending produces identical points
   :id: TEST_MIRROR_IDEMPOTENT
   :verifies: REQ_MIRROR_IDEMPOTENT

   ``tests/test_influx_mirror.py::test_resending_produces_identical_lines``

.. test:: Deployment templates are placeholder-only and complete
   :id: TEST_PORTABLE_CONFIG
   :verifies: REQ_PORTABLE_CONFIG

   ``tests/test_deploy.py::test_no_real_values_in_templates``,
   ``tests/test_deploy.py::test_example_env_values_are_placeholders``,
   ``tests/test_deploy.py::test_every_compose_variable_is_in_example_env``

Requirement → test table
------------------------

.. needtable::
   :types: req
   :columns: id, title, verifies_back
   :style: table

A ``needflow`` (graphviz) diagram is intentionally omitted: it would add a
system ``dot`` dependency the build otherwise does not need, and the table above
already shows the requirement → test mapping the gate enforces.
