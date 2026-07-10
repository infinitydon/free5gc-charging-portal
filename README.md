# free5GC Charging Portal

Small lab portal for topping up free5GC online charging quota.

The portal updates the same MongoDB charging data used by free5GC WebConsole/CHF:

`policyData.ues.chargingData`. The portal displays this configured/granted quota field directly; consumed usage is produced by free5GC CHF CDR processing once the SMF charging rule is attached to a chargeable PCC rule.

It then optionally calls the free5GC CHF recharge notification endpoint:

`PUT /nchf-convergedcharging/v3/recharging/{ueId}?ratingGroup={ratingGroup}`

This is intended for demos and lab validation. It is not a production billing system.

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `MONGO_URI` | `mongodb://mongodb:27017` | MongoDB URI for the free5GC database. |
| `MONGO_DB` | `free5gc` | free5GC Mongo database name. |
| `CHF_BASE_URL` | `http://chf:8000` | CHF SBI base URL. |
| `CHF_NOTIFY_ENABLED` | `true` | Send the CHF recharge notification after Mongo update. |
| `CHF_BEARER_TOKEN` | empty | Optional bearer token for CHF notification. |
| `PORTAL_TITLE` | `free5GC Charging Portal` | UI title. |
| `PORTAL_MODE` | `operator` | `operator` shows all subscribers; `user` shows only the detected subscriber. |
| `OPERATOR_PIN` | `admin123` | Demo operator PIN for operator top-ups. |
| `END_USER_SELF_TOPUP` | `true` | Allow subscriber self-service top-ups. |
| `TRUSTED_SUBSCRIBER_HEADER_ENABLED` | `false` | Allow a trusted ingress/proxy to pass the subscriber SUPI in a header. |
| `TRUSTED_SUBSCRIBER_HEADER` | `x-subscriber-supi` | Header name used when trusted subscriber header mode is enabled. |
| `SUBSCRIBER_BINDINGS_JSON` | `{}` | JSON map of source IP/CIDR to SUPI, for example `{"10.60.0.0/16":"imsi-208930000000001"}`. |
| `DEFAULT_SUBSCRIBER_SUPI` | empty | Optional lab-only fallback SUPI when no header or source-IP binding matches. |

## API

List charging records:

```bash
curl http://localhost:8080/api/charging-records
```

Top up:

```bash
curl -X POST http://localhost:8080/api/topups \
  -H 'content-type: application/json' \
  -d '{"ueId":"imsi-208930000000003","ratingGroup":1,"amountBytes":104857600,"actor":"operator","pin":"admin123"}'
```

Self top-up:

```bash
curl -X POST http://localhost:8080/api/topups/self \
  -H 'content-type: application/json' \
  -H 'x-subscriber-supi: imsi-208930000000003' \
  -d '{"ratingGroup":1,"amountBytes":10485760,"actor":"demo-user"}'
```

The self-service endpoint intentionally ignores browser-supplied subscriber IDs.
Run it behind a trusted identity-aware proxy or use source-IP/CIDR bindings for
lab UE traffic.
