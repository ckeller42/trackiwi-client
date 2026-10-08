trackiwi-client
===============

A read-only, standard-library-only Python client for a personal trackiwi GPS
account: it syncs positions into a local SQLite cache, exports them as GPX,
GeoJSON or CSV, estimates which way a parked vehicle points, and can mirror
the cache into InfluxDB for Grafana. It is unofficial and unaffiliated: trackiwi
publishes no API, so this talks to the private one its own app uses.

The pages are grouped by what the reader needs. The `README
<https://github.com/ckeller42/trackiwi-client#readme>`_ on GitHub stays the
short front door.

.. toctree::
   :maxdepth: 1
   :caption: Getting started

   getting-started

.. toctree::
   :maxdepth: 1
   :caption: How-to guides

   howto-sync-and-export
   howto-ingest

.. toctree::
   :maxdepth: 1
   :caption: Reference

   reference/cli
   reference/units
   reference/exports
   reference/glossary
   requirements
   traceability
   api

.. toctree::
   :maxdepth: 1
   :caption: Explanation

   architecture

.. toctree::
   :maxdepth: 1
   :caption: Contributing

   ops
