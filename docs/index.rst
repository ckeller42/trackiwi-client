trackiwi-client requirements & traceability
============================================

This is not end-user documentation — the README is. This build exists to hold
the project's requirements as first-class, linkable objects and to **fail** if
any requirement is not verified by a test.

* :doc:`architecture` — the system in C4 diagrams (context, containers, components,
  deployment) plus the ``ingest`` sequence and the cache schema.
* :doc:`requirements` — every requirement as a ``sphinx-needs`` ``req`` object.
* :doc:`traceability` — each requirement traced to the test(s) that verify it,
  plus the requirement → test table. The build fails (under ``-W``) if any
  ``req`` has no incoming ``verifies`` link.
* :doc:`api` — the API, rendered from docstrings, so each function's
  ``Implements :need:`REQ_…``` reference resolves against the requirements.

.. toctree::
   :maxdepth: 2
   :caption: Contents

   architecture
   requirements
   traceability
   api
