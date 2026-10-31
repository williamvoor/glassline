> ## Documentation Index
> Fetch the complete documentation index at: https://docs.fireworks.ai/llms.txt
> Use this file to discover all available pages before exploring further.

# Serverless Training

> Run LoRA training, preference optimization, and RL on the shared Fireworks Training API trainer pool with no provisioning and per-token pricing.

> [!NOTE]
>
> Serverless training is generally available. Start with the [quickstart](#quickstart), or [contact us](https://fireworks.ai/contact-training) for help choosing between shared and dedicated training.

Serverless training connects to a shared, already-running pooled trainer through the gateway and returns training and sampling clients. There is no trainer job or inference deployment to provision or delete. Close each sampler and the service client when finished.

Not sure whether the shared pool fits your workload? [Compare serverless and dedicated training](https://docs.fireworks.ai/fine-tuning/training-api/introduction.md#infrastructure).

## Supported models

See the [Models catalog](https://docs.fireworks.ai/fine-tuning/models.md) and set **Availability** to **Serverless** for the current model list and context limits. You attach to a shared, always-on trainer pool with a base model and your own `max_seq_len`. Serverless supports LoRA SFT, DPO, ORPO, RL, distillation, and custom loops.

* Checkpoint storage for serverless models is currently included at no charge.
* The model catalog evolves, and other frontier models are coming soon. For full-parameter training, a model not listed for Serverless, or explicit infrastructure control, use [Dedicated Training](https://docs.fireworks.ai/fine-tuning/training-api/dedicated.md).

> [!NOTE]
>
> **What you need**
>
> * **Install the SDK** (same one as the dedicated path, no separate console flow): `pip install "fireworks-ai[training]"`
> * **For the runnable example, clone the cookbook:** `git clone https://github.com/fw-ai/cookbook && pip install -e ./cookbook/training math-verify`
> * **Point at the serverless endpoint:** `base_url="https://api.fireworks.ai/training/v1/serverless"`
> * **Pick a supported base model.** See the [Models catalog](https://docs.fireworks.ai/fine-tuning/models.md), set **Availability** to **Serverless**, and verify current availability before launch.
> * Set your API key and run the [Quickstart](#quickstart) below.

> [!TIP]
>
> Start with the [DeepMath serverless example](https://github.com/fw-ai/cookbook/blob/main/training/examples/rl/deepmath/train_deepmath_serverless.py), which runs on the experimental [`async_rl_loop_serverless` adapter](https://github.com/fw-ai/cookbook/blob/main/training/recipes/experiment/async_rl_loop_serverless.py). Write your own task code against the same adapter when you need custom rollout functions, rollout/training overlap, or agentic trajectories. The adapter fits the dedicated `async_rl_loop` to the pooled serverless lifecycle and is expected to fold into the main recipe once both paths share one weight-publication contract.

## What is serverless training?

You write the loop for supervised training, preference optimization, reinforcement learning, distillation, or another custom method. Fireworks runs the forward pass, backward pass, and optimizer on remote GPUs, then serves your latest weights for sampling in the same session.

The [quickstart](#quickstart) shows the exact client setup and operation order. The Cookbook paths above cover both a simple loop and the experimental async adapter.

### Serverless lifecycle

1 · Local
**Your Python loop**
Builds batches, rewards, and optimizer requests.

2 · Shared
**Pooled trainer**
Runs remote LoRA forward, backward, and optimizer work.

3 · In session
**Sampler snapshot**
Captures the current adapter weights for sampling.

4 · Shared
**Pooled sampler**
Returns rollouts for local scoring and the next step.

You do not create or delete a trainer job or inference deployment. Close each sampler and the service client when the run finishes.

## What you can run

> [card] Supervised Fine-Tuning (SFT)
>
> Run the loop with a cross-entropy loss over your labeled data.

> [card] Direct Preference Optimization (DPO)
>
> Train from chosen/rejected preference pairs. LoRA DPO uses the policy session's shared base reference, so there is no separate reference trainer to provision.

> [card] Reinforcement Learning (RL)
>
> Sample completions from the current adapter, score them with your own reward function, and train with an importance-sampling loss (GRPO-style). This is the primary serverless use case.

> [card] Odds Ratio Preference Optimization (ORPO)
>
> Combine supervised learning on chosen responses with an odds-ratio preference objective, without a separate reference trainer.

> [card] Distillation and custom loops
>
> Train a student from teacher scores or implement another loss with the same Training API operations used on dedicated infrastructure.

These methods run as LoRA on the shared pool. Dedicated and serverless expose the same Training API method surface; the infrastructure and parameter-mode constraints differ. For full-parameter training, a model outside the serverless pool, or explicit infrastructure control, use [Dedicated Training](https://docs.fireworks.ai/fine-tuning/training-api/dedicated.md). For standard platform-managed jobs, use [Managed Training](https://docs.fireworks.ai/fine-tuning/managed-finetuning-intro.md).

## When to use dedicated

Use [Dedicated Training](https://docs.fireworks.ai/fine-tuning/training-api/dedicated.md) when you need full-parameter training, a model outside the serverless pool, explicit resource lifecycle control, or sustained utilization. See the canonical [serverless versus dedicated comparison](https://docs.fireworks.ai/fine-tuning/training-api/introduction.md#infrastructure).

## Core concepts

**Session.** `create_lora_training_client(base_model, rank)` attaches you to a pooled trainer for that base model. That attachment is your training session (`service.training_session_id`) — your LoRA state lives there.

**Run.** The training client is your run (`training_client.run_id`). One run is one training trajectory: the `forward_backward` and `optim_step` calls plus the checkpoints you save.

**LoRA adapter.** Serverless is LoRA only. Pass a positive `rank` (e.g. `rank=8`); base weights stay frozen and shared across the pool, and you train an adapter on top.

**Checkpoint / snapshot.** `save_weights_for_sampler(name)` writes your current adapter weights and returns a snapshot path. That path is a public sampler identity, not a raw storage URI — hand it to the sampler to serve exactly those weights. See [Saving and loading checkpoints](#saving-and-loading-checkpoints) for the full save / resume / promote surface.

**Sampling.** `create_sampling_client(model_path=snapshot, tokenizer=...)` returns a sampler bound to that snapshot through the completions API (`/inference/v1/completions`). The snapshot selects the weights to serve; it is not a prompt-cache affinity key. Sampling runs on the shared pool, so there's no deployment to create or hot-load, and it does not need a training client: any process can sample a base model or a saved checkpoint. See [Sampling without a training client](#sampling-without-a-training-client). Serverless and dedicated sampling share the [`DeploymentSampler` request contract](https://docs.fireworks.ai/fine-tuning/training-api/reference/deployment-sampler).

### Sampling with prompt caching

Check [Inference for RL rollouts](https://docs.fireworks.ai/guides/rollout-inference.md) for guidance on session affinity and KV-cache behavior. Its **Training SDK** tab shows how to pass a stable trajectory ID through `DeploymentSampler`.

Install SDK version 1.2.9 or later and restart the client process after upgrading:

```bash theme={null}
pip install --upgrade "fireworks-ai[training]>=1.2.9"
```

> [!WARNING]
>
> SDK versions 1.2.0 through 1.2.8 derived a shared session-affinity value from the sampler checkpoint, overriding per-request session values. Upgrade before using per-request affinity.

For batched rollout generation, see [`DeploymentSampler`: RL rollout sampling](https://docs.fireworks.ai/fine-tuning/training-api/reference/deployment-sampler#rl-rollout-sampling) for the two-samples-per-prompt pattern and `n=1` behavior.

## Quickstart

### Step 1: Create a key, install, and authenticate

Create an API key in the [Fireworks dashboard](https://app.fireworks.ai/settings/users/api-keys) (click **Create API key** and store it somewhere safe), or run `firectl api-key create`. Then install the SDK and export the key:

```bash theme={null}
git clone https://github.com/fw-ai/cookbook
pip install -e ./cookbook/training
pip install math-verify
export FIREWORKS_API_KEY="fw_..."   # the key you just created
```

The quickstart's DeepMath reward needs `math-verify` to grade answers that match only symbolically, such as `\frac{1}{2}` and `0.5`.

### Step 2: Run the complete serverless RL example

The cookbook's DeepMath example includes its dataset, reward, loop, metrics, and cleanup behavior. It runs GRPO on the bundled DeepMath dataset through the experimental [`async_rl_loop_serverless`](https://github.com/fw-ai/cookbook/blob/main/training/recipes/experiment/async_rl_loop_serverless.py) recipe. These flags keep the first run small:

```bash theme={null}
cd cookbook/training
python -m examples.rl.deepmath.train_deepmath_serverless \
  --base-model accounts/fireworks/models/muse-glimmer-30b \
  --tokenizer-model meta-models/Muse-Glimmer-30B --renderer-name muse_glimmer \
  --max-rows 32 --prompt-groups-per-step 8 --completions-per-prompt 4 \
  --max-completion-tokens 4096 --max-seq-len 8192 --lora-rank 8
```

This is a real paid run. Pass `--base-model` and `--tokenizer-model` to use another model in the serverless pool. Without the size flags (`--max-rows`, `--prompt-groups-per-step`, `--completions-per-prompt`, `--max-completion-tokens`), the example runs its full default on Kimi K3, which costs much more. Metrics and checkpoint paths are written to `--run-dir`.

The remaining snippets show the underlying SDK operation order. The recipe performs equivalent steps for you.

### Step 3: Connect to the serverless session

```python theme={null}
import os
from fireworks.training.sdk import FiretitanServiceClient

service = FiretitanServiceClient(
    api_key=os.environ["FIREWORKS_API_KEY"],
    base_url="https://api.fireworks.ai/training/v1/serverless",
)

base_model = "accounts/fireworks/models/qwen3p8-27b"
max_seq_len = 131072  # enforce this when rendering prompts and training datums
training_client = service.create_lora_training_client(base_model=base_model, rank=8)

print(f"session={service.training_session_id} run={training_client.run_id}")
```

This doubles as your setup check: if it prints a session id beginning with `ts-`, serverless routing is working. If the base URL does not end in `/training/v1/serverless`, sampling errors out.

### Step 4: Train, checkpoint, and sample

The complete example defines `datums`, `tokenizer`, `prompt`, and
`params`, and rejects prompt plus completion lengths above `max_seq_len`. The
excerpt below shows the operation order:

```python theme={null}
from fireworks.training.sdk import AdamParams

# One optimizer step
training_client.forward_backward(datums, "importance_sampling").result()
training_client.optim_step(
    AdamParams(learning_rate=2.5e-5, beta1=0.9, beta2=0.95, eps=1e-8, weight_decay=0.0)
).result()

# Save weights and open a sampler bound to that exact snapshot
snapshot = training_client.save_weights_for_sampler("step-0001").result().path
sampler = service.create_sampling_client(model_path=snapshot, tokenizer=tokenizer)
completions = sampler.sample(
    prompt=prompt,
    num_samples=8,
    sampling_params=params,
).result()
sampler.close()
```

> [!WARNING]
>
> * Remote training and sampling operations return future-like results. Call `.result()` on operations that return one, or failures can be missed.
> * The tokenizer you pass must match `base_model`. It renders prompts and decodes sampled tokens client-side, so a mismatch silently corrupts your rewards.
> * Give each checkpoint a distinct `name`. Saving twice with the same name currently resolves to the same snapshot path.

## Reinforcement learning example

The end-to-end serverless RL pattern is the standard GRPO / importance-sampling loop: each step saves the current adapter, rolls out a batch of prompts through a sampler bound to that snapshot, scores completions with your reward function, turns group-relative advantages into training datums, and takes one optimizer step.

Track reward over time; improvement depends on the task, data, reward function, and configuration. Use the cookbook [DeepMath serverless example](https://github.com/fw-ai/cookbook/blob/main/training/examples/rl/deepmath/train_deepmath_serverless.py) for a runnable task, or the [`async_rl_loop_serverless`](https://github.com/fw-ai/cookbook/blob/main/training/recipes/experiment/async_rl_loop_serverless.py) recipe it runs on for experimental async scheduling and custom rollout functions. For a supervised loop, use cross-entropy loss. For a preference loop, use the DPO loss over chosen/rejected pairs — see [Cookbook: DPO](https://docs.fireworks.ai/fine-tuning/training-api/cookbook/dpo.md) for the dataset format and loss details. For the broader RL loss menu and dedicated provisioning, see the [cookbook RL recipes](https://docs.fireworks.ai/fine-tuning/training-api/cookbook/rl.md).

## Evaluating serverless checkpoints

Evaluate a sampler checkpoint with `sampler.sample()`, during the run or after it ends. After the run, open the sampler from a fresh service client with the checkpoint path (see [Sampling without a training client](#sampling-without-a-training-client)). The sampler is not a serverless chat endpoint for the adapter.

To evaluate through the chat completions API, promote the sampler checkpoint and evaluate the promoted model with a preemptible deployment. Serverless sampling does not apply to an adapter produced by dedicated training.

See [Evaluating Trained Models](https://docs.fireworks.ai/fine-tuning/evaluating-fine-tuned-models.md) for both workflows and the dedicated training alternative.

## Sampling without a training client

Sampling does not need `create_lora_training_client`. A fresh `FiretitanServiceClient` can sample a base model in the serverless pool, or any sampler checkpoint your account saved with `save_weights_for_sampler`, including checkpoints from a run that has already ended. These samplers create no training run and do not use a [concurrent-run](#capacity-and-rate-limits) slot, so evaluation workers can open and close them freely. Sampled tokens are billed per token like other serverless training sampling.

> [!NOTE]
>
> Requires SDK version 1.2.29 or later: `pip install --upgrade "fireworks-ai[training]>=1.2.29"`.

### Sample a base model

```python theme={null}
import os
from fireworks.training.sdk import FiretitanServiceClient, types

service = FiretitanServiceClient(
    api_key=os.environ["FIREWORKS_API_KEY"],
    base_url="https://api.fireworks.ai/training/v1/serverless",
)
sampler = service.create_sampling_client(
    base_model="accounts/fireworks/models/qwen3p8-27b",
    tokenizer=tokenizer,
)
result = sampler.sample(
    prompt=types.ModelInput.from_ints(tokenizer.encode("The weather today is")),
    num_samples=1,
    sampling_params=types.SamplingParams(max_tokens=20, temperature=0.7),
).result()
sampler.close()
```

### Sample a saved checkpoint

Pass the path that `save_weights_for_sampler` returned, `<account>/<run-id>/<checkpoint-name>`. The process that saved it can be gone:

```python theme={null}
# In the training process
path = training_client.save_weights_for_sampler("step-0100").result().path

# Later, in any process with an API key for the same account
service = FiretitanServiceClient(
    api_key=os.environ["FIREWORKS_API_KEY"],
    base_url="https://api.fireworks.ai/training/v1/serverless",
)
sampler = service.create_sampling_client(model_path=path, tokenizer=tokenizer)
```

* You can only sample checkpoints that belong to your own account. A path for another account fails before any request is sent.
* To keep a checkpoint as a deployable model, [promote it](#promote-a-sampler-checkpoint-to-a-model).
* On a service that has already created a training client, `create_sampling_client(model_path=...)` for that run's checkpoints, and `base_model=...` for the session's base weights, sample through the training session as before.

### Share a sampler across processes

`FiretitanSamplingClient` works with Python multiprocessing. Create it once in the main process and pass it to workers; the same instance can be shared by several processes.

**Pickle the client.** The pickle carries the connection state, including the API key and headers, so workers need no environment of their own. Each worker gets its own event loop, HTTP connections, and concurrency controller; a fixed concurrency limit carries over.

```python theme={null}
from concurrent.futures import ProcessPoolExecutor

def evaluate(sampler, prompt):
    return sampler.sample(prompt=prompt, num_samples=1, sampling_params=params).result()

with ProcessPoolExecutor(max_workers=8) as pool:
    results = list(pool.map(evaluate, [sampler] * len(prompts), prompts))
```

**Pass a sampler handle.** To avoid putting credentials in a pickle, pass a string handle instead. It carries no API key or credential headers; the receiving process authenticates with `api_key=` or its own `FIREWORKS_API_KEY`.

```python theme={null}
handle = sampler.get_sampler_handle()  # str: store it, queue it, send it anywhere

# In the worker
from fireworks.training.sdk import FiretitanSamplingClient
sampler = FiretitanSamplingClient.from_sampler_handle(handle, tokenizer=tokenizer)
```

| Method | Description |
| - | - |
| Pickling | Carries the connection state, API key, headers, and tokenizer. Each unpickled copy gets its own event loop, HTTP connections, and concurrency controller; a fixed concurrency limit carries over. |
| `get_sampler_handle() -> str` | A pickle-free string handle to the same sampler, with no API key or credential headers. |
| `FiretitanSamplingClient.from_sampler_handle(handle, *, api_key=None, tokenizer=None)` | Creates a client for the sampler behind `handle`, in any process and any number of times. Authenticates with `api_key`, or `FIREWORKS_API_KEY` from the environment. |

Keep `FiretitanServiceClient` and training clients in the main process; only sampling clients are meant to cross process boundaries.

## Saving and loading checkpoints

Serverless training writes **two different kinds of checkpoint**, and they are not interchangeable:

| | **Training checkpoint** | **Sampler checkpoint** |
| - | - | - |
| Saved by | `save_state(name)` | `save_weights_for_sampler(name)` |
| Contains | Adapter weights **and** optimizer state | Current adapter weights only |
| Resumable — the trainer can load it back and keep training | ✅ Yes | ❌ No |
| Promotable to a deployable model | ❌ No | ✅ Yes |

> [!WARNING]
>
> Only **sampler checkpoints** can be promoted to a final model, and only **training checkpoints** can be loaded back by the trainer to resume. A training checkpoint cannot be served or promoted; a sampler checkpoint cannot restore training state (weights + optimizer).

Checkpoint storage is currently included at no charge.

### Save and resume training checkpoints

Save training checkpoints periodically so an interrupted run can continue:

```python theme={null}
# Save a training checkpoint (adapter weights + optimizer) for resume
training_client.save_state("step-0100").result()

# Resume inside the same run: restores weights and optimizer state
training_client.load_state_with_optimizer("step-0100").result()

# Weights-only load (optimizer resets to zero — a warm start)
training_client.load_state("step-0100").result()
```

Save and load calls return futures — call `.result()` to block and surface failures. `save_state` also accepts a `timeout` to bound the wait. Give each checkpoint a distinct name: overwriting an existing name (`overwrite=True`) is not supported.

> [!WARNING]
>
> **Keep checkpoint names to 17 characters or fewer.** The server-side id is `run-<32-hex>-<your-name>-<8-hex>` and must fit in 63 characters, leaving 17 for your name.
>
> This is only checked at promote time, so an overlong name fails after training completes. Recovery is `load_state` into a fresh session and re-saving under a shorter name.

To see what checkpoints a run has saved, use the session-scoped control-plane list in [Promote a sampler checkpoint to a model](#promote-a-sampler-checkpoint-to-a-model) below — the trainer-local `training_client.list_checkpoints()` is not routed on the serverless surface.

### Resume a new run from a training checkpoint

To fork a new run from a previous run's training checkpoint, use `create_training_client_from_state` on the service client. The SDK reads the base model and LoRA configuration from the checkpoint itself, creates a fresh run, and loads the saved state:

```python theme={null}
# Continue a previous run's training checkpoint in a new run (weights + optimizer)
resumed_client = service.create_training_client_from_state_with_optimizer(
    "<account>/<run-id>/step-0100",
)

# Or warm start from those weights with a fresh optimizer
resumed_client = service.create_training_client_from_state(
    "<account>/<run-id>/step-0100",
)
```

Checkpoint references are fully qualified as `<account>/<run-id>/<checkpoint-name>`, where `run-id` is the previous run's `training_client.run_id` (`run-<hex>`). `weights_access_token` is not supported — load checkpoints accessible to your API key.

### Promote a sampler checkpoint to a model

Promotion turns a **sampler checkpoint** into a deployable Fireworks model (a standard LoRA addon model). Training checkpoints are not promotable — save a sampler checkpoint first.

**1. Save a sampler checkpoint during training**

```python theme={null}
snapshot = training_client.save_weights_for_sampler("final").result().path
```

Save one at the end of training, and at any intermediate step you may want to deploy later.

**2. List the session's checkpoints**

Listing and promotion are session-scoped control-plane operations on `FireworksClient` (against the regular API gateway, not the serverless base URL). The session resource name is available as `service.training_session_name`:

```python theme={null}
import os
from datetime import datetime
from fireworks.training.sdk import FireworksClient

fw_client = FireworksClient(api_key=os.environ["FIREWORKS_API_KEY"])

rows = fw_client.list_training_session_checkpoints(service.training_session_name)
target = max(
    (row for row in rows if row.get("promotable")),
    key=lambda row: datetime.fromisoformat(row["createTime"].replace("Z", "+00:00")),
)
```

Each row carries `name` (the full 4-segment resource name), `checkpointName`, `checkpointType`, `promotable`, and `createTime`. Two things to know about the values:

* `checkpointName` is the server-side checkpoint id, **not** the bare name you passed: it is prefixed with the source run id and, for sampler checkpoints, suffixed with an 8-hex-char session id — a save named `final` surfaces as `run-<hex>-final-<8hex>`. Select rows on `promotable` + `createTime` as above, or with a prefix/substring test — never by equality with your logical name. The composed id caps your own name at [17 characters](#save-and-resume-training-checkpoints).
* `checkpointType` is a server enum string: `CHECKPOINT_TYPE_TRAINING_LORA` for training checkpoints, `CHECKPOINT_TYPE_INFERENCE_LORA` for sampler checkpoints. Treat it as opaque and filter on `promotable`, which is authoritative.

**3. Promote the checkpoint**

```python theme={null}
model = fw_client.promote_session_checkpoint(
    name=target["name"],  # accounts/<a>/trainingSessions/<s>/checkpoints/<c>
    output_model_id="my-serverless-lora",
    base_model="accounts/fireworks/models/qwen3p8-27b",
)
```

`output_model_id` must be 1-63 characters of lowercase a-z, 0-9, and hyphens. The promoted model appears in your account's model list like any other trained model.

> [!NOTE]
>
> Session-scoped list and promote require the training session and its bound trainer to still exist. Once the session is deleted or its trainer is drained, both calls return `NOT_FOUND` and the checkpoints are no longer reachable through this API — promote any checkpoint you want to keep before the session is torn down. Cross-run training-checkpoint resume (above) is resolved per run and is not subject to this limit.

### Deploy the promoted model to production

A promoted model deploys like any LoRA model trained on Fireworks: with **live merge**, Fireworks merges the adapter into the base weights at deployment time, so the deployment performs identically to the base model.

> [!WARNING]
>
> Trained LoRA models can only be deployed to **on-demand (dedicated) deployments**. Serverless per-token serving of your own trained LoRA is not available.

Deploy the promoted model directly:

```bash theme={null}
firectl deployment create "accounts/<account-id>/models/my-serverless-lora" \
  --deployment-shape default
```

To pick a specific shape instead of `default`, see [Deployment shapes](https://docs.fireworks.ai/guides/ondemand-deployments.md#deployment-shapes).

Then send requests with the model name:

```python theme={null}
from fireworks import Fireworks

client = Fireworks()
response = client.chat.completions.create(
    model="accounts/<account-id>/models/my-serverless-lora",
    messages=[{"role": "user", "content": "Hello!"}],
)
```

For deployment configuration, performance, and troubleshooting, see [Deploying trained models](https://docs.fireworks.ai/fine-tuning/deploying-loras.md).

For the full SDK-level checkpoint reference (base/delta sampler types, weight sync, and cross-job resolution), see [Dedicated Training](https://docs.fireworks.ai/fine-tuning/training-api/dedicated.md#saving-and-loading) and the [Cookbook Reference](https://docs.fireworks.ai/fine-tuning/training-api/cookbook/reference.md#checkpoints).

## Pricing

Serverless training is billed per token, across three meters: prefill, sample, and train. Rates are USD per 1 million tokens. Available models and rates can change, so verify current availability in the [Models catalog](https://docs.fireworks.ai/fine-tuning/models.md) and the [pricing page](https://fireworks.ai/pricing) before launch.

| Base model | Context | Prefill | Cached prefill | Sample | Train |
| - | - | - | - | - | - |
| Muse Spark Open | 256K | \$6.19 | \$1.239 | \$15.48 | \$18.58 |
| DeepSeek V4.1 Flash | 256K | \$0.60 | \$0.12 | \$3.60 | \$4.98 |
| DeepSeek V4.1 Flash 1017 | 256K | \$0.60 | \$0.12 | \$3.60 | \$4.98 |
| NVIDIA Nemotron 3.5 Super VL 120B | 256K | \$0.57 | \$0.114 | \$1.44 | \$2.32 |
| GLM 5.3 Flash | 200K | \$2.96 | \$0.593 | \$7.41 | \$8.89 |
| GLM 5.3 | 262K | \$4.86 | \$0.972 | \$12.15 | \$14.58 |
| Qwen 3.6 35B A3B | 256K | \$0.45 | \$0.09 | \$1.335 | \$2.14 |
| Qwen 3.8 27B | 128K | \$1.86 | \$0.372 | \$5.595 | \$4.103 |
| Kimi K3 | 192K | \$10.87 | \$2.17 | \$27.11 | \$32.55 |
| DeepSeek V4 Flash 0731 | 262K | \$1.74 | \$0.35 | \$4.33 | \$5.20 |
| Muse Glimmer 30B | 128K | \$1.96 | \$0.39 | \$4.88 | \$5.86 |

Nemotron 3.5 Super VL uses the current Tinker 256K card, which includes a limited-time 50% discount. DeepSeek V4.1 Flash 1017 uses the same rates as DeepSeek V4.1 Flash.

### What the meters mean

Serverless training bills three separate token meters, so a run's cost depends on the mix of rollout and training tokens your loop generates:

| Meter | What it counts |
| - | - |
| **Prefill** | Prompt tokens processed by the sampler before generation. Repeated prefixes hit the cache and bill at the cached rate, an 80% discount. |
| **Sample** | Tokens generated by the sampler during rollouts. |
| **Train** | Tokens passed through `forward_backward`, i.e. the datums you train on. |

### Capacity and rate limits

* **Concurrent runs:** The default is 8 active runs per account, although account overrides may differ. Some pool models also limit active runs per account on that base model, and managed fine-tuning jobs on the same model count toward it. Over either limit, new runs fail with `RESOURCE_EXHAUSTED`. A run holds its slot until its session is deleted or expires. [Sampling without a training client](#sampling-without-a-training-client) uses no slot.
* **Free a slot now:** Closing the client does not end the session right away. To release its slot, promote any checkpoint you need, then delete the session (`service.training_session_id`, which starts with `ts-`):

  ```bash theme={null}
  curl -X DELETE \
    -H "Authorization: Bearer $FIREWORKS_API_KEY" \
    https://api.fireworks.ai/v1/accounts/<ACCOUNT_ID>/trainingSessions/<SESSION_ID>
  ```
* **Request and token limits:** Contact Fireworks for the current limits on your account.
* **Shared-pool capacity:** if the pool is full, `create_lora_training_client` returns an out-of-capacity error; retry, or switch to the dedicated path.

### Behavior to know

* **Set `max_seq_len` explicitly.** Serverless has no dedicated instance to infer sequence length from.
* **Cross-run checkpoint resume.** A training checkpoint can be resumed inside the same run (`load_state_with_optimizer`), or forked into a new run with `create_training_client_from_state` / `create_training_client_from_state_with_optimizer` using a fully qualified `<account>/<run-id>/<checkpoint-name>` reference. See [Saving and loading checkpoints](#saving-and-loading-checkpoints).
* **Serving your trained adapter.** Sample its sampler checkpoints with a sampling client, during or after the run. To serve it through an API endpoint, [promote a sampler checkpoint to a model](#promote-a-sampler-checkpoint-to-a-model) and [deploy it on an on-demand dedicated deployment](#deploy-the-promoted-model-to-production); serverless per-token serving of your own trained LoRA is not available.

## Video walkthrough: Train a prompt router

This walkthrough trains Qwen 3.5 9B with LoRA SFT to classify prompts and route them to a small or large model. It covers the local Python loop, pooled serverless trainer, checkpoint evaluation, and the before-and-after comparison.

> [!WARNING]
>
> This walkthrough uses the deprecated Qwen 3.5 9B pool and is retained for conceptual reference. Use Qwen 3.8 27B for current Serverless Training runs.

[Train a prompt router with Fireworks Serverless Training](https://www.youtube.com/embed/0NMrs9X36mg)

> [card] [Open the current prompt-router serverless notebook](https://github.com/fw-ai/cookbook/blob/main/training/case-studies/sft_prompt_router/prompt_router_serverless.ipynb)
>
> Follow the current Serverless Training example for dataset preparation, LoRA SFT, checkpoint sampling, and evaluation.

## Next steps

* [DeepMath serverless example](https://github.com/fw-ai/cookbook/blob/main/training/examples/rl/deepmath/train_deepmath_serverless.py): runnable serverless RL loop
* [Async serverless RL recipe](https://github.com/fw-ai/cookbook/blob/main/training/recipes/experiment/async_rl_loop_serverless.py): experimental rollout-function loop with rollout/training overlap
* [Choose infrastructure](https://docs.fireworks.ai/fine-tuning/training-api/introduction.md#infrastructure): compare serverless and dedicated
* [Dedicated Training](https://docs.fireworks.ai/fine-tuning/training-api/dedicated.md): the provisioned path from setup through teardown
* [Training and Sampling](https://docs.fireworks.ai/fine-tuning/training-api/dedicated.md#training-and-sampling): dedicated lifecycle internals
* [Loss Functions](https://docs.fireworks.ai/fine-tuning/training-api/dedicated.md#loss-functions): built-in and custom losses
* [The Cookbook](https://docs.fireworks.ai/fine-tuning/training-api/cookbook/overview.md): ready-to-run recipes, including the [DeepMath serverless example](https://github.com/fw-ai/cookbook/blob/main/training/examples/rl/deepmath/train_deepmath_serverless.py)
