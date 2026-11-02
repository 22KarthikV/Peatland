# Job queue (contract C4)

Contract version: 1. Message body schema: `queue.schema.json` (JSON Schema 2020-12). Examples:
`fixtures/queue.pipeline.json`, `fixtures/queue.change.json`.

## Queues

| Queue | Purpose |
|---|---|
| `peatwatch-jobs` | Standard SQS queue (LocalStack locally, AWS in Terraform). One message per `runs` row. |
| `peatwatch-jobs-dlq` | Dead-letter queue for `peatwatch-jobs`. |

- **Producer:** the API inserts the `runs` row in `queued`, then sends the message. If the send
  fails, the run goes to `failed` with error `enqueue_failed` (db-schema.md section 4).
- **Consumer:** the worker. It validates the body against `queue.schema.json`, then claims the run
  with the compare-and-set `queued -> running`. Zero rows updated means another worker has it or
  it was cancelled: delete the message and move on.
- Standard queue means at-least-once delivery and no ordering. The compare-and-set and the product
  idempotency keys (cog-layout.md section 6) make redelivery safe.

## Settings

| Setting | Value | Why |
|---|---|---|
| `maxReceiveCount` (redrive policy) | 3 | After 3 receives without a delete the message moves to the DLQ. |
| Visibility timeout | 900 s, extended by the worker while it runs | The worker heartbeats `runs.heartbeat_at` and extends visibility; after a crash the message reappears within 15 minutes. |
| Message retention | 4 days (`peatwatch-jobs`), 14 days (DLQ) | Time to notice and redrive. |
| Long polling | 20 s | Fewer empty receives. |

## Delivery behaviour

1. **Success:** the run reaches `succeeded`; the worker deletes the message.
2. **Handled failure** (for example `no_usable_scenes`): the worker sets the run to `failed` with
   `error`, then deletes the message. Retrying is the user resubmitting the request, which creates
   a new run (db-schema.md section 3, runs). The queue does not retry handled failures.
3. **Crash or timeout:** the message is not deleted, reappears after the visibility timeout and is
   received again. If the run is still `queued`, or `running` with a stale heartbeat, the worker
   takes it over; finished steps are skipped by their idempotency keys.
4. **Poison message:** an invalid body (schema violation, unknown `contract_version`, unknown
   site or model) will never succeed. The worker does not delete it, so it reaches the DLQ after 3
   receives. When `run_id` is readable the worker also marks the run `failed` with error
   `step_failed`.
5. **DLQ arrival:** a reaper (or an alarm on the DLQ's `ApproximateNumberOfMessagesVisible` above
   0) fails any run that is still `queued` or `running` for a DLQ message, with error
   `worker_lost`, so the status screen never hangs.
6. **Redrive:** after a fix, an operator redrives DLQ messages to `peatwatch-jobs` (SQS redrive).
   Redriving a message whose run is already terminal is a no-op: the compare-and-set claims
   nothing, and the worker deletes the message.

## Compatibility

`contract_version` is a number. A worker rejects versions it does not know (poison path above).
Adding an optional property is a non-breaking change, but the schema has
`additionalProperties: false`, so producers must not send a property before the schema lists it.
