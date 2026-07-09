# free5GC Charging Portal

Small lab portal for topping up free5GC online charging quota.

The portal updates the same MongoDB charging data used by free5GC WebConsole/CHF:

`policyData.ues.chargingData`

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
| `OPERATOR_PIN` | `admin123` | Demo operator PIN for operator top-ups. |
| `END_USER_SELF_TOPUP` | `true` | Allow self-service fictitious top-ups. |

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
  -d '{"ueId":"imsi-208930000000003","ratingGroup":1,"amountBytes":10485760,"actor":"demo-user"}'
```
