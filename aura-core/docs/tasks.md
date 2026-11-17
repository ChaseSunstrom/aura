# Tasks: the record, and the thing that runs them

Two files, and the split is the whole design.

`aura/tasks.py` is the **record**: what is happening, how far it has got, what
it called, what it printed. It runs nothing, and says so.

`aura/taskengine.py` is the **engine**: a bounded queue, a small pool of
workers, retries with backoff, and work that survives a restart.

They were one thing short of useful for a while. `run_background_task` minted an
id, fired an event nobody listened to, and told the model to say the result
would arrive later; Jarvis Code and research each grew their own unbounded
`ensure_future`. Three jobs at once meant three conversations against one model
server with one KV cache, and the symptom was not an error — it was everything
becoming four times slower at once, which reads as "Jarvis is broken today".

## Submitting work

```python
engine = jarvis.taskengine
task = await jarvis.tasks.async_add("Read twelve pages", kind="research")
engine.submit(task.id, worker, kind="research", retries=1)
```

A worker is `async def worker(task_id: str) -> None`. It reports through the
registry, calls `aura.tasks.raise_if_cancelled(task_id)` wherever stopping is
safe, and raises to fail. The engine owns *when* work runs and *how often it is
retried*, and knows nothing about what the work is.

## The concurrency limit

`llm.max_concurrent` (default 2) is the number of jobs that may run at once,
because every one of them eventually talks to the same model server. The queue
is what makes exceeding it impossible rather than merely discouraged; a
submission past `MAX_QUEUED` is refused with a message a person can read.

Not everything queues. A scheduled `notify` fires on the spot: a reminder
waiting behind a twenty-minute coding job is a reminder that arrives after the
thing it was reminding you about. Only `research` and `code` — the kinds that
hold a model conversation — go through the queue.

## Retries

`retries=n` gives a failure `n` more attempts, with backoff of 2s, 8s, 32s,
capped and jittered so three jobs failing against the same dead model server do
not retry in lockstep for ever. A `TaskCancelled` is not a failure and is never
retried: somebody asked it to stop and it did.

## What a restart does

The queue is not saved. A worker is a closure in the process that submitted it,
and nothing can rebuild one from what is stored, so there is no queue to bring
back. Each task is left as its record says, and `TaskEngine.load` settles the
rest on start-up:

* **queued** work ends **error**, "was waiting when Jarvis stopped, and nothing
  has it now". Nothing will run it, and a task left at "queued" would sit there
  for ever.
* **running** work ends **error**, "interrupted when Jarvis restarted", and is
  not run again. Re-running half of something that already half-happened is a
  worse failure than reporting that it stopped.
* a **finished** task stays finished.

Older stores that still carry a `queue` key or a per-task `resumed` flag load
fine: the queue entry is ignored and the flag is dropped.

## Retrying by hand

`jarvis/tasks/retry` (WS) puts a finished task back on the queue — the button
somebody presses after fixing whatever broke. It works within one process. After
a restart the worker that ran the task is gone, so the route answers 400 ("nothing
on this server knows how to run ... work again") rather than queueing a task that
would never run.
