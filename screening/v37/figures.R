# Figure 2 of the v37 manuscript. Reads census_tables.json, std_rates.json and validation outputs;
# writes PDF (quartz) and PNG (ragg, 300 dpi) to figures/. Run from v37/: Rscript figures.R
suppressPackageStartupMessages({library(ggplot2); library(jsonlite); library(cowplot)})

OUT <- "figures"
dir.create(OUT, showWarnings = FALSE)
FONT <- "Arial"; PT <- 7 / .pt
BLUE <- "#2C7BB6"; ORANGE <- "#E66101"; GREY <- "#636363"; LIGHT <- "#F2F2F2"

save_fig <- function(p, name, w, h) {
  quartz(type = "pdf", file = file.path(OUT, paste0(name, ".pdf")), width = w, height = h, family = FONT)
  print(p); invisible(dev.off())
  ragg::agg_png(file.path(OUT, paste0(name, ".png")), width = w, height = h, units = "in", res = 300)
  print(p); invisible(dev.off())
}
n <- function(x) format(x, big.mark = ",", scientific = FALSE, trim = TRUE)

# ---------------------------------------------------------------- Figure 2: rate by year
ct <- fromJSON("census_tables.json"); sr <- fromJSON("std_rates.json")
yrs <- as.character(2016:2026)
crude <- data.frame(year = 2016:2026, rate = sapply(yrs, function(y) ct[[y]]$wrong_paper_per_1000_printed))
std <- rbind(
  data.frame(year = 2024:2026 + 0.12, rate = unlist(sr$publisher_groups[c("2024", "2025", "2026")]),
             series = "Standardised to 2024 mix of 17 publisher groups"),
  data.frame(year = 2024:2026 + 0.24, rate = unlist(sr$journals[c("2024", "2025", "2026")]),
             series = "Standardised to 2024 mix of 724 journals"))
crude$series <- "Crude, all reviews"
lev <- c("Crude, all reviews", "Standardised to 2024 mix of 17 publisher groups", "Standardised to 2024 mix of 724 journals")
crude$series <- factor(crude$series, lev); std$series <- factor(std$series, lev)

f2 <- ggplot() +
  geom_line(data = crude, aes(year, rate), colour = GREY, linewidth = 0.5) +
  geom_point(data = crude, aes(year, rate, colour = series, shape = series), size = 1.8) +
  geom_point(data = std, aes(year, rate, colour = series, shape = series), size = 1.8) +
  geom_text(data = crude, aes(year - ifelse(year >= 2024, 0.22, 0), rate, label = sprintf("%.2f", rate)), vjust = -1, size = PT * 0.95,
            family = FONT, colour = GREY) +
  scale_colour_manual(values = c(GREY, BLUE, ORANGE), name = NULL) +
  scale_shape_manual(values = c(16, 15, 17), name = NULL) +
  scale_x_continuous(breaks = 2016:2026, labels = c(2016:2025, "2026\nJan-Jun")) +
  scale_y_continuous(limits = c(0, 7), breaks = 0:7, expand = expansion(mult = c(0, 0.02))) +
  labs(x = "Year of review publication",
       y = "Printed DOIs or PMIDs that belong\nto a different work, per 1,000") +
  theme_minimal(base_size = 7, base_family = FONT) +
  theme(panel.grid.minor = element_blank(), panel.grid.major.x = element_blank(),
        axis.text = element_text(colour = "black"), legend.position = "inside",
        legend.position.inside = c(0.02, 0.98), legend.justification = c(0, 1),
        legend.key.height = unit(9, "pt"), legend.background = element_rect(fill = "white", colour = NA))
save_fig(f2, "Figure_2", 6.5, 3.2)

# Figure 1 is drawn by fig1_diagram.py (diagram-design skill); this script now draws Figure 2 only.
cat("done\n")
