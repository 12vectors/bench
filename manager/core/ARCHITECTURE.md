# Bench architecture

placeholder

```architecture current
L0  config
L1  state reports templates
L2  taskfiles
L3  plans profiles
L4  reviews
L5  verify
L6  progress
L7  acceptance
L8  gates
L9  required
L10 doctor
L11 commands drive events sync
L12 github
L13 delivery
L14 agentsettings
L15 settings
L16 setupguide
L17 agents
L18 setuptasks
L19 readiness
L20 previews
L21 phases
L22 refine
L23 httpd watch
L24 board
composition: board httpd watch
```

```architecture proposed
L0  gitproc records redact
L1  config
L2  state reports templates gitrepo
L3  githost
L4  taskfiles
L5  profiles plans
L6  gatepolicy reviews
L7  evidence resultidentity
L8  staleness
L9  verify
L10 progress
L11 acceptance
L12 gates
L13 required doctor
L14 events drive sync commands
L15 changes
L16 completion
L17 stacks
L18 delivery
L19 agentsettings
L20 settings
L21 setupguide
L22 agents
L23 setuptasks
L24 readiness previews
L25 phases
L26 refine
L27 watch routes
L28 httpd
L29 board
composition: board httpd routes
retired: github
new: gitproc records redact gitrepo githost gatepolicy evidence resultidentity staleness changes completion stacks routes
```

```architecture counts
top-level-exceptions 0
function-imports 27
private-reaches 68
dynamic-patch-targets 1
provider-vocabulary 195
routes 91
```
