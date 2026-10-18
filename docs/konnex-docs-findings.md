# Konnex official documentation — what was verified (fetched 2026-10-18 from docs.konnex.world)

Sources fetched: /sdk/sdk.md, /sdk/cli.md, /sdk/python.md, /sdk/http.md, /sdk/robots.md, /sdk/validators.md,
/subnets-workload-classes/subnets.md, /understand-konnex/contracts-and-popw.md, /participate/mining.md, /understand-konnex/roadmap.md

## Verified (as documented)
- SDK install from source: `git clone https://github.com/konnex-world/konnex.git`; `pip install -e ./sdk/python-client ./sdk/cli ./tools`; Python 3.10.
  - NOTE: on 2026-10-18 https://github.com/konnex-world/konnex returned HTTP 404 from our environment -> SDK source NOT obtainable; nothing installed or run.
- CLI (docs): `konnex robot attach --robot-id <id> --subnet <subnet> --keys ./keys.json`;
  `konnex robot record --job-id <JOBID> --sensors camera,imu,torque,thermal --out ./powp.zip`;
  `konnex prove --job-id <JOBID> --bundle ./powp.zip`; `konnex task submit --subnet ... --prompt ... --reward-usd1 ... --stake-usd1 ... --deadline ...`.
  Validators page states commands are "illustrative".
- Python (docs): `from konnex import KonnexClient`; `client.tasks.submit(subnet, prompt, reward_usd1, stake_usd1, deadline_s)`;
  `client.miners.register(...)`; `client.validators.verify(job_id, bundle_path)`.
- HTTP (docs): POST /api/v1/tasks {subnet,prompt,rewardStable,stakeStable,deadline}; POST /api/v1/miners/models; POST /api/v1/validators/verify {jobId,bundleUri}. Base URL/auth "match your deployment" (not published).
- PoPW binds: task instruction (signed, hashed onchain), policy execution trace, sensor bundle (hardware-signed via TEE/secure element where assurance applies), independent validator scoring.
- Testnet subnets: drone navigation, roboarm VLA, SLAM 3D map (example id in docs: sn.roboarm.sim). Builders page allows "Propose your own subnet" and lists "Sensor fusion & PoPW validation".

## Unverified / open questions
- No published base URL, auth scheme, keys.json format, or PoPW bundle (powp.zip) internal layout.
- No subnet accepts fishing/tension-sensor tasks today; HookPilot would be a proposed new subnet (or fit "Sensor fusion & PoPW validation").
- Whether `--sensors` accepts custom channels (e.g. "tension", "motor_current").
- Task schema for non-VLA, deterministic-controller workloads (no "policy.wasm").
- Secure-element signing requirements / supported hardware.
=> Adapter must be a LOCAL MOCK, clearly labeled; real adapter shape mirrors documented CLI/HTTP shapes only, behind a feature flag, unimplemented network calls raise NotImplementedError("unverified").
