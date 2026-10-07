# Position fields and units

The field names in the feed do not say what unit they use and the vendor documents nothing.
These were verified against a live account and recorded drives (see the `AGENTS.md` gotchas
and the design spec it names). Read this before doing arithmetic on an export.

| Field | Unit | Notes |
|---|---|---|
| `fix_at` | epoch seconds, UTC | Normalised on parse: the sync CSV carries an integer that is seconds or milliseconds (anything above 10,000,000,000 is read as milliseconds), the trackers endpoint an ISO 8601 string. A zone-less ISO value is read as UTC. A value outside the representable range makes the row skipped and counted. |
| `fix_timezone` | integer offset | Not a zone name. Its interpretation is unconfirmed; nothing depends on it, because every timestamp is handled and exported in UTC. |
| `latitude`, `longitude` | decimal degrees | Non-finite values make the row skipped and counted. |
| `speed` | km/h | Reported speed is 3.6 times the speed derived from consecutive positions. InfluxDB field `speed_kmh`. |
| `altitude` | metres | InfluxDB field `altitude_m`. |
| `course` | degrees clockwise from true north | Matches the bearing between fixes. GPS course over ground, so unreliable at low speed. InfluxDB field `course_deg`. |
| `distance` | centimetres | A per-fix delta reported by the device from its own consecutive readings, not an odometer. It can disagree slightly with the distance computed from two stored coordinates when a fix was dropped or GPS jittered. Do not correct it. |
| `voltage` | centivolts | `1303` means 13.03 V. |
| `id` | integer | Server position id. The sync offset is the highest id seen. |
| `tracker_id` | integer | Device id. |
| `rssi`, `sat`, `battery` | as sent | Stored as received. |

The local `positions` table has the columns of `trackiwi.COLUMNS`, in that order, which is
also the column order of the `csv` export. The InfluxDB point format (measurement
`trackiwi_position`, units in the field names) is built by `trackiwi/lineprotocol.py`; see
`REQ_LINEPROTOCOL_UNITS` in the [requirements](../requirements.rst).
