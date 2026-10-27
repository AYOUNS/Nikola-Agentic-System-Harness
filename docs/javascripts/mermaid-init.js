/*
 * Mermaid bootstrap.
 *
 * mkdocs-carbon ships no diagram support, so the renderer is loaded from the CDN
 * in `extra_javascript` (see mkdocs.yml) and started here. `startOnLoad` waits for
 * DOMContentLoaded on its own, which matters because the carbon base template
 * emits extra_javascript in <head>.
 *
 * `securityLevel: "strict"` keeps HTML in node labels escaped; `<br/>` is still
 * honoured for line breaks. `theme: "base"` is required for themeVariables to
 * apply — under "default" mermaid keeps its own palette and only a few variables
 * (font, line colour) pass through.
 */
mermaid.initialize({
  startOnLoad: true,
  theme: "base",
  securityLevel: "strict",
  flowchart: { useMaxWidth: true, htmlLabels: true },
  themeVariables: {
    fontFamily: "'IBM Plex Sans', system-ui, sans-serif",
    // Carbon tokens, matching docs/stylesheets/carbon-tokens.css
    primaryColor: "#edf5ff",        // blue-10  — node fill
    primaryBorderColor: "#0f62fe",  // blue-60  — node stroke
    primaryTextColor: "#161616",    // gray-100 — node label
    secondaryColor: "#f4f4f4",      // gray-10  — subgraph fill
    tertiaryColor: "#ffffff",
    lineColor: "#525252",           // gray-70  — edges
    textColor: "#161616",
    mainBkg: "#edf5ff",
    nodeBorder: "#0f62fe",
    clusterBkg: "#f4f4f4",
    clusterBorder: "#c6c6c6",
    edgeLabelBackground: "#ffffff",
  },
});
