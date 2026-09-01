#!/usr/bin/env Rscript

# TPC-H full-pipeline scaling plot following SPARQLprov Figure 3.

library(dplyr)
library(ggplot2)
library(scales)
library(grid)

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 2) {
  stop("usage: plot_figure7.R OUTPUT_ROOT MEASURED_FULL_PIPELINE_CSV")
}
root <- normalizePath(args[[1]], mustWork = FALSE)
input_path <- normalizePath(args[[2]], mustWork = TRUE)
figure_dir <- file.path(root, "figures")
dir.create(figure_dir, recursive = TRUE, showWarnings = FALSE)

results <- read.csv(input_path, check.names = FALSE, stringsAsFactors = FALSE)
required <- c(
  "template", "scale_factor", "engine", "mode", "status",
  "runtime_ms", "timeout_s", "data_kind"
)
missing <- setdiff(required, colnames(results))
if (length(missing) > 0) {
  stop(paste("missing input columns:", paste(missing, collapse = ", ")))
}
if (nrow(results) != 540) {
  stop(paste("expected 540 rows (12 templates x 9 scales x 5 series), found", nrow(results)))
}
if (any(results$data_kind != "measured")) {
  stop("Figure 7 accepts measured rows only")
}

paper_scales <- 10^((0:8) / 4 - 2)
observed_scales <- sort(unique(results$scale_factor))
if (length(observed_scales) != 9 || any(abs(log10(observed_scales) - log10(paper_scales)) > 1e-10)) {
  stop("input does not use the fixed nine TPC-H scale factors")
}

template_order <- c(
  "Q01", "Q03", "Q04", "Q05", "Q06", "Q07",
  "Q08", "Q10", "Q12", "Q14", "Q15", "Q19"
)
engine_order <- c("GraphDB 10.7.6", "Oxigraph 0.5.9", "PostgreSQL 18.4")
mode_order <- c("ProvSQL", "SPARQLcirc (flat)", "SPARQLcirc (factored)")

results <- results %>%
  mutate(
    runtime_ms = suppressWarnings(as.numeric(runtime_ms)),
    template = factor(template, levels = template_order,
                      labels = sprintf("template %02d", as.integer(sub("Q", "", template_order)))),
    engine = factor(engine, levels = engine_order),
    mode = factor(mode, levels = mode_order),
    series = interaction(engine, mode, drop = TRUE)
  ) %>%
  arrange(template, engine, mode, scale_factor)

if (any(results$status == "ok" & (!is.finite(results$runtime_ms) | results$runtime_ms <= 0))) {
  stop("successful rows require a positive measured runtime")
}
if (any(results$status != "ok" & !is.na(results$runtime_ms))) {
  stop("non-successful rows must not carry a plotted runtime")
}

scale_breaks <- c(0.01, 0.1, 1)
scale_break_labels <- parse(text = c("10^{-2}", "10^{-1}", "10^{0}"))
figure_font_scale <- 1.5

p <- ggplot(
    results,
    aes(
      x = scale_factor,
      y = runtime_ms,
      group = series,
      color = engine,
      shape = engine,
      linetype = mode
    )
  ) +
  facet_wrap(~template, ncol = 4) +
  geom_line(linewidth = 0.45, na.rm = FALSE) +
  geom_point(size = 1.55, stroke = 0.4, na.rm = TRUE) +
  scale_x_continuous(
    trans = "log10",
    breaks = scale_breaks,
    labels = scale_break_labels
  ) +
  scale_y_continuous(
    trans = "log10",
    labels = trans_format("log10", math_format(10^.x))
  ) +
  xlab("scale factor") +
  ylab("runtime (ms)") +
  theme_minimal(base_family = "Times", base_size = 11 * figure_font_scale) +
  scale_color_manual(name = "engine", values = c(
    "GraphDB 10.7.6" = "#F8766D",
    "Oxigraph 0.5.9" = "#00BFC4",
    "PostgreSQL 18.4" = "#CC79A7"
  )) +
  scale_shape_manual(name = "engine", values = c(
    "GraphDB 10.7.6" = 16,
    "Oxigraph 0.5.9" = 17,
    "PostgreSQL 18.4" = 15
  )) +
  scale_linetype_manual(name = "mode", values = c(
    "ProvSQL" = "dashed",
    "SPARQLcirc (flat)" = "solid",
    "SPARQLcirc (factored)" = "dotted"
  )) +
  guides(
    color = guide_legend(order = 1, nrow = 1, override.aes = list(linetype = 1)),
    shape = guide_legend(order = 1, nrow = 1),
    linetype = guide_legend(order = 2, nrow = 1, override.aes = list(color = "#333333"))
  ) +
  theme(
    legend.position = "top",
    legend.box = "vertical",
    legend.box.just = "center",
    legend.spacing.y = unit(-6, "pt"),
    legend.key.spacing.x = unit(12, "pt"),
    legend.title.position = "left",
    legend.title = element_text(
      family = "Times", size = 10.5 * figure_font_scale, face = "bold",
      lineheight = 1, vjust = 0.5, margin = margin(0, 15, 0, 0, "pt")
    ),
    legend.text = element_text(
      family = "Times", size = 8.8 * figure_font_scale,
      face = "plain", lineheight = 1, vjust = 0.5
    ),
    legend.box.margin = margin(0, 0, 0, 0, "pt"),
    legend.box.spacing = unit(0, "pt")
  )

png(
  file = file.path(figure_dir, "figure_7.png"),
  res = 600, width = 6000, height = 4200, pointsize = 13
)
print(p)
dev.off()

pdf(
  file = file.path(figure_dir, "figure_7.pdf"),
  width = 10, height = 7, family = "Times"
)
print(p)
dev.off()
