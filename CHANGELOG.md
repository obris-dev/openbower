# Changelog

## 0.0.1 (2026-10-06)


### Features

* a door's trouble is transport and data, never a bad agent ([#15](https://github.com/obris-dev/openbower/issues/15)) ([7effbe4](https://github.com/obris-dev/openbower/commit/7effbe444e3861e9c429b476ce8ee51a392f7cf3))
* a local install signs in against the hosted hub ([#72](https://github.com/obris-dev/openbower/issues/72)) ([69f1644](https://github.com/obris-dev/openbower/commit/69f164472d21310e0bbaadca6dde768d205dd5eb))
* a row's work is one kind-blind loop ([#47](https://github.com/obris-dev/openbower/issues/47)) ([0195690](https://github.com/obris-dev/openbower/commit/0195690a985ccdcb4595eb5d2ab5f6ca0acf269c))
* agents (roster, builder, test bench, spec-door providers) ([#5](https://github.com/obris-dev/openbower/issues/5)) ([176590a](https://github.com/obris-dev/openbower/commit/176590a851b09a69056773bc701681ac9b90aac4))
* AI columns (durable fills, diagnosed cells, spend consent) ([#6](https://github.com/obris-dev/openbower/issues/6)) ([cf54d49](https://github.com/obris-dev/openbower/commit/cf54d4925374ee7b2512c9339c85769e47fb82b2))
* async row-push webhook (POST /v1/lists/{id}/ingest) ([#29](https://github.com/obris-dev/openbower/issues/29)) ([eaf229f](https://github.com/obris-dev/openbower/commit/eaf229f5ba4f3caded0d8973437944f44a2d86eb))
* auth end to end in the browser ([#2](https://github.com/obris-dev/openbower/issues/2)) ([8989d87](https://github.com/obris-dev/openbower/commit/8989d8799b78f3ec1cc7d663f589973732b45811))
* column reorder (header drag, per-column menu) ([#7](https://github.com/obris-dev/openbower/issues/7)) ([4c4fa0b](https://github.com/obris-dev/openbower/commit/4c4fa0b4e4e1806e18132884e9448679c3447c18))
* core verifies machine tokens by relaying to the hub (no secret) ([#27](https://github.com/obris-dev/openbower/issues/27)) ([d19bfe6](https://github.com/obris-dev/openbower/commit/d19bfe6647c7a86ab944ba4a5345d0c54ccd1ef3))
* discover end to end (proxy, contract, web surface) ([#3](https://github.com/obris-dev/openbower/issues/3)) ([ebfa376](https://github.com/obris-dev/openbower/commit/ebfa37694412a24c138ad778f15632d92c0d3fd4))
* ingest push overrides AI columns + type-validates at the POST ([#36](https://github.com/obris-dev/openbower/issues/36)) ([270391f](https://github.com/obris-dev/openbower/commit/270391f891f92a7f47a037102ce4192540fb25c7))
* Kafka ingest bus + deduping worker (apply is a log for now) ([#30](https://github.com/obris-dev/openbower/issues/30)) ([ffa60e0](https://github.com/obris-dev/openbower/commit/ffa60e0f33bbab8e421aeea5fa1f6f8b4d4b8b75))
* serper replaces dataforseo as the metered search door ([#23](https://github.com/obris-dev/openbower/issues/23)) ([f6f25f7](https://github.com/obris-dev/openbower/commit/f6f25f7aa5696c1afe7656de07c4fe853b9b4d29))
* the ingest worker appends pushed rows to the sheet ([#31](https://github.com/obris-dev/openbower/issues/31)) ([71111db](https://github.com/obris-dev/openbower/commit/71111db7aa2079a49827961cfb7265d0d7fd8a82))
* the marketing site as its own web app (@bower/marketing, :3005) ([#12](https://github.com/obris-dev/openbower/issues/12)) ([2e950b3](https://github.com/obris-dev/openbower/commit/2e950b31f3057a6fdb029ee8bdc6804594575aa8))
* the Send webhook column persists ([#41](https://github.com/obris-dev/openbower/issues/41)) ([aae66df](https://github.com/obris-dev/openbower/commit/aae66df2ef1f6a8e9213d9f416db7b1ec1c2a756))
* the Send webhook column tab, whose one action is Test ([#40](https://github.com/obris-dev/openbower/issues/40)) ([ddc94dd](https://github.com/obris-dev/openbower/commit/ddc94dd2660995b4e0a1a43d0abbc78373a3ba2b))
* the workflow substrate (Workflow / NodePath / Node, agent_id -&gt; node_id) ([#38](https://github.com/obris-dev/openbower/issues/38)) ([db6fe4f](https://github.com/obris-dev/openbower/commit/db6fe4f979990c653093c50fb478626dcb6e147e))
* unify fill + autofill on one task state machine (provision/process split) ([#35](https://github.com/obris-dev/openbower/issues/35)) ([a5afe9c](https://github.com/obris-dev/openbower/commit/a5afe9c38b6797e045e438ceccece92ac7e51110))
* webhook destinations and the settings area ([#39](https://github.com/obris-dev/openbower/issues/39)) ([d6c905b](https://github.com/obris-dev/openbower/commit/d6c905b2e472b305910c0dca67f207b61c02cb69))


### Bug fixes

* drop the account-reassurance line from the sign-in-failed card ([#14](https://github.com/obris-dev/openbower/issues/14)) ([760cbdf](https://github.com/obris-dev/openbower/commit/760cbdf49be7a11f89e1a2baed28ffaf9016392a))
* drop the spend footer from the AI column drawer ([#8](https://github.com/obris-dev/openbower/issues/8)) ([5812938](https://github.com/obris-dev/openbower/commit/5812938de260727878a26e4d86c3460faa044de2))
* hold the List lock over the columns write, not the queue insert ([#10](https://github.com/obris-dev/openbower/issues/10)) ([69a1889](https://github.com/obris-dev/openbower/commit/69a18894834368a64a9178ca006f7ea4cd670e63))
* machine-auth review follow-ups (core) ([#28](https://github.com/obris-dev/openbower/issues/28)) ([dd2720f](https://github.com/obris-dev/openbower/commit/dd2720f4dcb581a7545385ef6ab62aef42e53389))
* the consent echo refuses growth only ([#11](https://github.com/obris-dev/openbower/issues/11)) ([b8da449](https://github.com/obris-dev/openbower/commit/b8da44906f461e71f252585a93e6df1694b73ce2))
