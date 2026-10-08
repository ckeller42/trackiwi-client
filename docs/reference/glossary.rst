Glossary
========

.. glossary::
   :sorted:

   Tracker
      A GPS device on the trackiwi account, identified by an integer id.

   Position
   Fix
      One reported location row of a tracker, with a server id and a ``fix_at`` time.

   Offset
      The id after which the next sync page starts; exclusive.

   Cache
      The local SQLite database of positions.

   Mirror
      The step that sends cached positions to InfluxDB.

   Mirror position
      The highest id acknowledged by one InfluxDB target, stored per target in ``mirror_state``.

   Ingest
      ``sync``, refresh tracker names, then mirror: what a timer runs.

   Requirement
      A ``sphinx-needs`` object with a ``REQ_`` id, traced to its verifying tests.
