"""The agent tools, as a registry package: `base` is the CONTRACT a
tool implements (ToolSpec, ToolError, and the helpers every tool
shares, importing no runtime and no seam), `registry` the guarded
index, `harness` the one catch site, and each FAMILY a package of
tools + errors + machinery (`search/`). DELIBERATELY inert: the
roster lives in AgentsConfig.ready(), Django's registration point, so
importing any submodule here never re-enters a half-initialized
package."""
