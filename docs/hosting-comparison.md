# Render vs Fly.io — Selenium2Playwright

Pricing checked September 7, 2026. All estimates are USD/month, before tax.

> **DECIDED 2026-09-07: Fly.io, operating PostgreSQL and Redis ourselves** —
> about $23/month. Config and script in [deploy/fly/](../deploy/fly/). One
> finding made after this comparison was written flipped the Postgres column and
> is recorded in the next paragraph. The Agent Server licensing entitlement below
> remains genuinely open.

**The finding that settled it.** The server's migrations create **three**
extensions, not one: `vector` (store index), `ltree` (`000029`) and `btree_gin`
(`000017`). Each is wrapped in `IF NOT EXISTS`, so a *pre-installed* extension
needs no privilege — but *creating* one needs superuser. Fly's Managed Postgres
offers only `vector` and PostGIS, by dashboard toggle: it would clear `vector`
and then fail on `ltree`, which is the same class of failure that ended the
LangSmith attempt ([deploy.md](deploy.md) §9). So the "Fly: managed Postgres"
column is not merely expensive, it does not work for this image. Running
`pgvector/pgvector:pg16` ourselves makes us superuser and all three succeed —
and it is also the cheapest column. Render's managed Postgres does support all
three and permits `CREATE EXTENSION`, so Render remains viable; it costs roughly
double for that convenience.

**Original recommendation, kept for the record:** Render for the first public
demo if approximately $65/month of infrastructure is acceptable. Fly.io can
reduce that to approximately $25–30/month if we operate PostgreSQL and Redis
ourselves. Both have the hosting capabilities this project needs.

The recommendation prioritizes getting the demo online with managed databases after the deployment failures documented in [deploy.md](deploy.md). This is a documentation and code review, not a successful deployment or a load test on either provider.

## What the repository actually needs

The deployment described in [deploy.md](deploy.md) uses the agent image, PostgreSQL with pgvector, and Redis. [langgraph.json](../langgraph.json) packages Python 3.12 and Node 22 plus the pinned TypeScript/ESLint toolchain. The image was measured at 1.6 GB; image size is not a RAM measurement.

The agent needs writable temporary workspace for its static validators, streaming HTTP, outbound model/embedding/tracing API access, and durable threads and memories. The planned [Streamlit playground](../plan.md) is another running Python service. Production validation does not launch browsers or execute uploaded tests, so this estimate includes neither GPU hosting nor browser workers.

## Feature fit

| Requirement | Render | Fly.io |
| --- | --- | --- |
| Existing Docker image; Python + Node subprocesses | Supported by Docker services | Supported by Machines running container images |
| PostgreSQL and semantic memory | Managed Postgres supports `CREATE EXTENSION vector` | Managed Postgres includes pgvector; alternatively run our pgvector image |
| Redis for streaming/background-run coordination | Managed Key Value, currently Valkey 8, with Redis-compatible clients | Run Redis on a private Machine; managed Upstash is an alternative requiring compatibility review |
| Long runs and streamed results | Persistent web services support SSE and WebSockets | Persistent Machines; configure HTTP idle timeout and shutdown grace |
| Thread persistence, interrupt/resume, cross-thread memory | Supported by the application's PostgreSQL-backed runtime | Same |
| Streamlit playground | A separate Python web service | A separate Machine |
| HTTPS, secrets, private service networking | Available | Available |
| Database backups | Paid Postgres includes PITR | Managed Postgres includes backups; self-managed Postgres requires our recovery process |
| Automatic scaling | Requires Pro workspace for horizontal autoscaling | Available, but the agent must retain running capacity |

These are platform capabilities, not proof that our exact image has passed integration checks. In particular, Render's Valkey service replaces the Redis 6 used in our local test; streaming, reconnects, and resume need smoke testing. Sources: [Render web services](https://render.com/docs/web-services), [Render pgvector support](https://render.com/docs/postgresql-extensions), [Render Key Value](https://render.com/docs/key-value), [Render backups](https://render.com/docs/postgresql-backups), [Render workspace features](https://render.com/docs/platform-features-by-plan), [Fly configuration](https://fly.io/docs/reference/configuration/), [Fly private networking](https://fly.io/docs/networking/private-networking/), and [Fly Managed Postgres](https://fly.io/docs/mpg/).

## Monthly infrastructure estimate

Assumptions: a light-traffic demo, one operator, one region, continuous operation, about 5 GB outbound traffic, 10 GB PostgreSQL storage, and one agent instance. Start with 2 GB agent RAM and low conversion concurrency. This is a sizing hypothesis; measure peak RAM during conversion before increasing concurrency. The optional playground gets 512 MB initially.

Fly compute amounts use the published pricing table's displayed rates for a 30-day month. Its selected region and calendar-month length can change the actual charge. No reservations, introductory credits, or legacy free allowances are assumed.

| Component | Render: managed data services | Fly: self-managed data services | Fly: managed Postgres, self-managed Redis |
| --- | ---: | ---: | ---: |
| Agent, 2 GB RAM | $25.00, 1 CPU | $11.83, shared-cpu-2x | $11.83, shared-cpu-2x |
| PostgreSQL compute, 1 GB RAM | $19.00 | $5.92, shared-cpu-1x | $38.00, Basic cluster |
| PostgreSQL storage, 10 GB | Budget $3.00 | $1.50 | $2.80 |
| Redis/Valkey | $10.00, managed 256 MB | $3.32, 512 MB Machine | $3.32, 512 MB Machine |
| Redis persistent volume, 1 GB | Included | $0.15 | $0.15 |
| Outbound traffic, 5 GB | Included | $0.10, North America/Europe rate | $0.10, North America/Europe rate |
| Workspace subscription | $0, Hobby | $0 | $0 |
| **Backend total** | **About $57** | **About $23** | **About $56** |
| Planned Streamlit playground | +$7.00 | +$3.32 | +$3.32 |
| **Backend + playground** | **About $64** | **About $26** | **About $60** |

Rates: [Render official pricing table](https://render.com/pricing.md), [Fly resource pricing](https://fly.io/docs/about/pricing/), and [Fly Managed Postgres pricing](https://fly.io/docs/mpg/#pricing). Render's pricing Markdown mentions 1 GB included, while its [cost guide](https://render.com/articles/how-much-does-cloud-application-hosting-cost-for-small-businesses) budgets provisioned GB at $0.30 each; the estimate conservatively allows $3 for 10 GB, a possible $0.30 overestimate.

Render can fall to **about $51 including the playground** using its $6/256 MB Postgres tier. Treat that as a lean experiment: test migrations, connection pools, checkpoint writes, and vector queries before selecting it. The 1 GB database is the recommended starting estimate.

The Fly managed database includes high availability; Render's Basic database and our proposed self-managed Fly database do not. Therefore these are practical purchasing options, not equivalent availability configurations. Both application estimates still have a single agent instance.

Fly's managed Postgres documentation currently lists security patches/version upgrades and customer-facing alerting as under development. Clarify its maintenance coverage before choosing it for production. [Managed Postgres feature status](https://fly.io/docs/mpg/).

## What changes the decision or the bill

- **Operational work:** On the inexpensive Fly option, we own PostgreSQL/Redis configuration, patching, backups, restore testing, and recovery. Fly labels its unmanaged Postgres product unsupported. Managed Postgres removes much of that work, but Redis in this estimate is still ours. [Fly pricing and unmanaged-product status](https://fly.io/docs/about/pricing/).
- **CPU:** Fly shared CPUs have a 6.25% baseline per vCPU and burst above it. Repeated TypeScript compiles can exhaust that burst balance. A 2 GB performance-1x agent is $32.19 at the displayed rate, adding approximately $20.36/month to either Fly column. This is why the cheaper VM is not a throughput-equivalent comparison. [CPU behavior](https://fly.io/docs/machines/cpu-performance/), [compute prices](https://fly.io/docs/about/pricing/).
- **Team access:** Render Hobby permits one workspace member. Pro adds $25/month and includes unlimited members and 25 GB bandwidth; compute remains additional. The recommended Render setup becomes approximately $89 with Pro. Hobby includes 5 GB, then $0.15/GB. [Current workspace plans](https://render.com/docs/new-workspace-plans).
- **Extra usage:** Model calls, embeddings, LangSmith tracing/subscriptions/runtime licensing, taxes, domain registration, artifact storage, paid support, and excess build/backup/traffic usage are excluded. Fly volume snapshots include the first 10 GB of stored snapshot data; additional storage is $0.08/GB/month. [Fly storage billing](https://fly.io/docs/about/billing/).
- **LLM budget:** Historical [project experiments](reflection-shootout-table.md) recorded $0.29–$0.69 per 12 files across the fully costed configurations. That implies roughly $24–$58 per 1,000 similarly sized conversions, as an illustration only. Different models, input lengths, retries, and current provider rates can change it substantially.

## Conditions before deployment

Keep the standalone agent running continuously. LangChain explicitly advises against scale-to-zero for this topology because background work can be lost. Render's free web service sleeps, and Fly's idle autostop must be disabled for the agent. The current LangChain guidance also recommends Kubernetes for its regularly tested production path; these small PaaS setups require us to configure graceful shutdown and run draining. [Standalone deployment guidance](https://docs.langchain.com/langsmith/deploy-standalone-server).

The existing notes show a local image starting with a LangSmith API key. Current official standalone documentation also lists `LANGGRAPH_CLOUD_LICENSE_KEY`, and the pricing page places self-hosted deployment options under Enterprise/custom pricing. A local startup is not sufficient evidence that hosted use has no runtime fee. **Confirm the entitlement for our pinned image and account before treating these as all-in totals; do not assume the $39 Plus subscription covers standalone hosting.** This uncertainty applies to both providers. [Standalone prerequisites](https://docs.langchain.com/langsmith/deploy-standalone-server), [LangSmith plans](https://www.langchain.com/pricing).

The playground, its rate limits/access controls, and remote suite upload still need application work. Single-file inline input is implemented; suite mode still expects server-side folders. Neither host fills those gaps. The current standalone guide uses `DATABASE_URI`, whereas our deployment notes mention `POSTGRES_URI`; use the variable required by the pinned image and verify it at startup.

For Render, use the full vector-enabled configuration, enable `vector` with the database owner, keep database and Key Value connections private, and test a complete conversion, semantic recall, interrupt/resume, and a restart. No cloud resources were created for this comparison.
