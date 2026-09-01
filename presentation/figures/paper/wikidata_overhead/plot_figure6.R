#!/usr/bin/env Rscript

# Adapted directly from SPARQLprov's data_analysis/experiment-3.Rmd, which
# produced Figure 4 (Wikidata time overhead). The facet, 100% stacked-bar,
# percentage-axis, Times-family, minimal-theme, and ggplot2 default-color
# choices are retained; only the input schema and bar grouping are changed.

library(dplyr)
library(ggplot2)
library(scales)

args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 2) {
  stop("usage: plot_figure6.R OUTPUT_ROOT QUERY_STAGE_TIMES_CSV")
}
root <- normalizePath(args[[1]], mustWork = FALSE)
input_path <- normalizePath(args[[2]], mustWork = TRUE)
figure_dir <- file.path(root, "figures")
dir.create(figure_dir, recursive = TRUE, showWarnings = FALSE)

results <- read.csv(input_path, check.names = FALSE, stringsAsFactors = FALSE)
required <- c(
  "query_id", "category", "compiler", "method", "status",
  "provenance_acquisition_ms", "artifact_preparation_ms",
  "pqe_overhead_ms", "total_e2e_ms"
)
missing <- setdiff(required, colnames(results))
if (length(missing) > 0) {
  stop(paste("missing input columns:", paste(missing, collapse = ", ")))
}

results <- results %>%
  filter(
    status == "ok",
    category %in% c("single", "multiple", "optional", "property_path"),
    compiler %in% c("CUDD", "d4"),
    method %in% c("C-flat", "C-factored"),
    total_e2e_ms > 0
  ) %>%
  mutate(
    compiler = factor(ifelse(compiler == "d4", "D4", compiler), levels = c("CUDD", "D4")),
    category = factor(category, levels = c("single", "multiple", "optional", "property_path")),
    method = factor(method, levels = c("C-flat", "C-factored")),
    provenance_acquisition_share = provenance_acquisition_ms / total_e2e_ms,
    artifact_preparation_share = artifact_preparation_ms / total_e2e_ms,
    pqe_overhead_share = pqe_overhead_ms / total_e2e_ms
  )

if (nrow(results) == 0) stop("no successful rows available for plotting")

# As in SPARQLprov Figure 4, component shares are first computed per query and
# then averaged within a query class. Timeout and failed cells are absent.
grouped <- results %>%
  group_by(compiler, category, method) %>%
  summarise(
    successful_queries = n(),
    mean_provenance_acquisition_ms = mean(provenance_acquisition_ms),
    mean_artifact_preparation_ms = mean(artifact_preparation_ms),
    mean_pqe_overhead_ms = mean(pqe_overhead_ms),
    mean_total_e2e_ms = mean(total_e2e_ms),
    mean_provenance_acquisition_share = mean(provenance_acquisition_share),
    mean_artifact_preparation_share = mean(artifact_preparation_share),
    mean_pqe_overhead_share = mean(pqe_overhead_share),
    .groups = "drop"
  ) %>%
  arrange(compiler, category, method)

components <- bind_rows(
  grouped %>% transmute(
    compiler, category, method, successful_queries,
    value = mean_provenance_acquisition_share,
    component = "circuit construction"
  ),
  grouped %>% transmute(
    compiler, category, method, successful_queries,
    value = mean_artifact_preparation_share,
    component = "circuit parsing"
  ),
  grouped %>% transmute(
    compiler, category, method, successful_queries,
    value = mean_pqe_overhead_share,
    component = "compilation and WMC"
  )
) %>%
  mutate(
    component2 = factor(
      component,
      levels = c("compilation and WMC", "circuit parsing", "circuit construction")
    ),
    category_label = recode(
      as.character(category),
      single = "Single-\nBGP",
      multiple = "Multi-\nBGP",
      optional = "Optionals",
      property_path = "Property\nPath"
    ),
    bar = factor(
      paste(category_label, ifelse(method == "C-flat", "flat", "factored"), sep = "\n"),
      levels = c(
        "Single-\nBGP\nflat", "Single-\nBGP\nfactored",
        "Multi-\nBGP\nflat", "Multi-\nBGP\nfactored",
        "Optionals\nflat", "Optionals\nfactored",
        "Property\nPath\nfactored"
      )
    ),
    x_position = seq(1.00, 4.00, by = 0.50)[as.numeric(bar)]
  )

# This is the SPARQLprov Figure 4 plotting block with facets mapped from
# engines to compilers and its three overhead components mapped to ours.
# Generic serif maps to the Times family on the renderers used here and avoids
# platform-specific font aliases while retaining the paper's typography.
plot_family <- "serif"
p_components <- ggplot(components, aes(x_position, fill = component2)) +
  # SPARQLprov used weight=value with stat="identity". ggplot2 4 requires
  # the equivalent explicit y aesthetic.
  geom_bar(aes(y = value), stat = "identity", width = 0.38) +
  facet_wrap(~compiler, nrow = 1) +
  scale_x_continuous(
    breaks = seq(1.00, 4.00, by = 0.50),
    labels = levels(components$bar),
    limits = c(0.72, 4.28),
    expand = expansion(mult = c(0, 0))
  ) +
  scale_y_continuous(
    labels = scales::percent,
    breaks = seq(0, 1, by = 0.25),
    limits = c(0, 1),
    expand = expansion(mult = c(0, 0.02))
  ) +
  ylab("") +
  xlab("query class and plan") +
  labs(fill = "") +
  guides(fill = guide_legend(nrow = 1, byrow = TRUE, reverse = TRUE)) +
  theme_minimal(base_family = plot_family) +
  theme(
    panel.spacing.x = grid::unit(0.45, "lines"),
    axis.text.x = element_text(size = 12.5, lineheight = 0.88, colour = "black"),
    axis.text.y = element_text(size = 14, colour = "black"),
    axis.title.x = element_text(size = 18.75 * (11.50 / 8.40), margin = margin(t = 10)),
    strip.text = element_text(size = 15),
    legend.text = element_text(size = 18.75 * (11.50 / 8.40)),
    legend.position = "top",
    legend.direction = "horizontal",
    legend.justification = "center",
    legend.key.height = grid::unit(0.9, "lines"),
    legend.key.width = grid::unit(0.9, "lines"),
    legend.spacing.x = grid::unit(0.35, "lines"),
    legend.margin = margin(t = -4, r = 0, b = -4, l = 0),
    plot.margin = margin(t = 5.5, r = 0.5, b = 5.5, l = -10)
  )

ggsave(
  file.path(figure_dir, "figure_6.png"),
  p_components,
  width = 11.50,
  height = 2.65,
  units = "in",
  dpi = 500,
  bg = "white"
)
ggsave(
  file.path(figure_dir, "figure_6.pdf"),
  p_components,
  width = 11.50,
  height = 2.65,
  units = "in",
  device = cairo_pdf,
  bg = "white"
)
