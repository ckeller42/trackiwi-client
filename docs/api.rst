API reference
=============

Rendered from the package docstrings so that each ``Implements :need:`REQ_…```
reference resolves against :doc:`requirements`. An unresolved ``:need:`` here
fails the build under ``-W``, which catches a typo'd requirement ID.

``trackiwi``
------------

.. automodule:: trackiwi
   :members:
   :undoc-members:

``trackiwi.client``
-------------------

.. automodule:: trackiwi.client
   :members:
   :undoc-members:
   :private-members: _redact, _check, _require_https, _coerce

``trackiwi.store``
------------------

.. automodule:: trackiwi.store
   :members:
   :undoc-members:
   :special-members: __enter__

``trackiwi.export``
-------------------

.. automodule:: trackiwi.export
   :members:
   :undoc-members:
   :private-members: _by_tracker, _finite

``trackiwi.heading``
--------------------

.. automodule:: trackiwi.heading
   :members:
   :undoc-members:
   :private-members: _bearing_between, _approach

``trackiwi.cli``
----------------

.. automodule:: trackiwi.cli
   :members:
   :undoc-members:
   :private-members: _write_atomically, _heading_line
