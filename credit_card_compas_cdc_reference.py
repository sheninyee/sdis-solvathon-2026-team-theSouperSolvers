library(shiny)
library(ggplot2)
library(dplyr)
library(tidyr)
options(shiny.maxRequestSize = 200 * 1024^2)
#  -------- Ref part-- --------
bank_names <- c(
  "BANK OF AMERICA, NATIONAL ASSOCIATION" = "Bank of America"
)

concern_map <- c(
  "Problem with a purchase shown on your statement" = "Billing/Disputes",
  "Advertising and marketing, including promotional offers" = "Marketing"
)

REFERENCE <- datQ |>
  filter(Company %in% names(bank_names)) |>
  mutate(
    bank = unname(bank_names[as.character(Company)]),
    
    concern = unname(concern_map[as.character(Issue)]),
    concern = coalesce(concern, "Other"),
    
    response = trimws(as.character(Company.response.to.consumer))
  ) |>
  filter(
    !is.na(response),
    response != "",
    response != "In progress"
  ) |>
  group_by(bank, concern) |>
  summarise(
    n = n(),
    monetary = mean(response == "Closed with monetary relief"),
    nonmonetary = mean(response == "Closed with non-monetary relief"),
    .groups = "drop"
  ) |>
  mutate(
    total = monetary + nonmonetary,
    no_relief = 1 - total
  )


# -----

CONCERNS <- c("Account Management", "Application/Approval", "Billing/Disputes",
              "Customer Service", "Marketing", "Other", "Payments")
CONCERN_COLORS <- setNames(c("#4477AA", "#EE6677", "#228833", "#CCBB44",
                             "#66CCEE", "#BBBBBB", "#AA3377"), CONCERNS)
OUTCOME_COLORS <- c("Monetary relief" = "#2563EB", "Non-monetary relief" = "#F59E0B",
                    "No recorded relief" = "#CBD5E1")
ISSUE_MAP <- c(
  "Problem with a purchase shown on your statement" = "Billing/Disputes",
  "Fees or interest" = "Billing/Disputes",
  "Incorrect information on your report" = "Billing/Disputes",
  "Getting a credit card" = "Application/Approval",
  "Closing your account" = "Account Management",
  "Trouble using your card" = "Customer Service",
  "Problem when making payments" = "Payments",
  "Advertising and marketing, including promotional offers" = "Marketing"
)
BANK_NAMES <- c("BANK OF AMERICA, NATIONAL ASSOCIATION" = "Bank of America",
                "CAPITAL ONE FINANCIAL CORPORATION" = "Capital One",
                "JPMORGAN CHASE & CO." = "Chase", "WELLS FARGO & COMPANY" = "Wells Fargo")
REFERENCE$total <- REFERENCE$monetary + REFERENCE$nonmonetary
REFERENCE$no_relief <- pmax(0, 1 - REFERENCE$total)

clean_text <- function(x, fallback) {
  x <- trimws(as.character(x)); x[is.na(x) | x == ""] <- fallback; x
}
prepare_records <- function(dat) {
  names(dat) <- make.names(sub("^\ufeff", "", names(dat)), unique = TRUE)
  required <- c("Company", "Issue", "Company.response.to.consumer", "Date.received")
  absent <- setdiff(required, names(dat))
  if (length(absent)) stop("Missing columns: ", paste(absent, collapse = ", "),
                           ". Please upload the original row-level CSV, not a summary table.")
  if (!nrow(dat)) stop("The CSV has no rows.")
  company <- clean_text(dat$Company, "(Missing company)")
  bank <- unname(BANK_NAMES[company]); bank[is.na(bank)] <- company[is.na(bank)]
  issue <- clean_text(dat$Issue, "(Missing issue)")
  concern <- unname(ISSUE_MAP[issue]); concern[is.na(concern)] <- "Other"
  response <- clean_text(dat$Company.response.to.consumer, "(Missing response)")
  # First try ISO YYYY-MM-DD dates (including timestamp exports), then US dates.
  text_date <- clean_text(dat$Date.received, "")
  date <- suppressWarnings(lubridate::ymd(substr(text_date, 1, 10), quiet = TRUE))
  missing <- is.na(date)
  date[missing] <- as.Date(suppressWarnings(lubridate::parse_date_time(
    text_date[missing], orders = c("mdy HMS", "mdy HM", "mdy", "ymd HMS", "ymd"),
    quiet = TRUE, tz = "UTC")))
  tibble(bank = bank, issue = issue, concern = concern, response = response,
         date = date, year = lubridate::year(date),
         product = if ("Product" %in% names(dat)) clean_text(dat$Product, "(Missing product)") else "(Product not supplied)",
         eligible = !response %in% c("In progress", "(Missing response)"))
}
aggregate_records <- function(records) {
  records |> filter(eligible) |> group_by(bank, concern) |>
    summarise(n = n(), monetary = mean(response == "Closed with monetary relief"),
              nonmonetary = mean(response == "Closed with non-monetary relief"), .groups = "drop") |>
    mutate(total = monetary + nonmonetary, no_relief = pmax(0, 1 - total))
}
bank_totals <- function(stats) {
  stats |> group_by(bank) |>
    summarise(monetary = weighted.mean(monetary, n),
              nonmonetary = weighted.mean(nonmonetary, n), n = sum(n), .groups = "drop") |>
    mutate(total = monetary + nonmonetary, no_relief = pmax(0, 1 - total))
}
rank_banks <- function(stats, weights, min_n = 20) {
  active <- names(weights)[weights > 0]
  if (!length(active) || !nrow(stats)) return(tibble())
  # Use identical categories for every bank; do not silently renormalize missing ones.
  tidyr::expand_grid(bank = unique(stats$bank), concern = active) |>
    left_join(stats, by = c("bank", "concern")) |>
    mutate(weight = unname(weights[concern])) |>
    group_by(bank) |>
    summarise(coverage = sum(!is.na(n)), sufficient = all(!is.na(n) & n >= min_n),
              score = weighted.mean(total, weight), n = sum(n, na.rm = TRUE), .groups = "drop") |>
    mutate(eligible = coverage == length(active) & sufficient,
           score = ifelse(eligible, score, NA_real_)) |>
    arrange(desc(eligible), desc(score), bank)
}
quiz_weights <- function(q1, q2, q3, q4) {
  w <- setNames(rep(1, length(CONCERNS)), CONCERNS)
  bump <- function(c, x) w[c] <<- pmax(w[c], x)
  if (q1 == "Building credit history") bump("Application/Approval", 2)
  if (q1 == "Everyday spending & rewards") { bump("Marketing", 2); bump("Billing/Disputes", 1.3) }
  if (q1 == "Balance transfer / paying down debt") { bump("Payments", 2); bump("Billing/Disputes", 1.5) }
  if (q1 == "Large purchase / financing") { bump("Application/Approval", 1.5); bump("Payments", 1.5) }
  if (q2 == "Yes, regularly") { bump("Payments", 2); bump("Billing/Disputes", 1.5) }
  if (q2 == "Sometimes") bump("Payments", 1.3)
  if (q3 == "Yes, and it was a hassle to resolve") bump("Billing/Disputes", 2.5)
  if (q3 == "Yes, but it went fine") bump("Billing/Disputes", 1.3)
  if (q4 == "Very important") bump("Customer Service", 2.5)
  if (q4 == "Somewhat important") bump("Customer Service", 1.5)
  w
}
wrap_label <- function(x, width = 29) vapply(x, function(s) paste(strwrap(s, width), collapse = "\n"), character(1))
chart_theme <- function() {
  theme_minimal(base_size = 12) + theme(
    text = element_text(color = "#1b2a41"), plot.background = element_rect(fill = "#fbfcfe", color = NA),
    panel.grid.major.y = element_blank(), panel.grid.minor = element_blank(),
    legend.position = "bottom", legend.title = element_blank(),
    axis.title = element_text(color = "#647084"), plot.title = element_text(face = "bold"),
    plot.margin = margin(12, 20, 12, 12))
}
card <- function(title, ..., subtitle = NULL) {
  div(class = "compass-card", h3(title), if (!is.null(subtitle)) p(class = "muted", subtitle), ...)
}
kpi <- function(label, value, detail = NULL) {
  column(3, div(class = "kpi", div(class = "eyebrow", label), div(class = "kpi-value", value),
                div(class = "muted", detail)))
}
notice <- function(...) div(class = "notice", ...)

# One consistent 100% outcome chart for dashboard, bank profiles and risks.
outcome_chart <- function(dat, key, threshold = 20, title = NULL) {
  dat <- dat |> arrange(total, .data[[key]])
  dat$bar <- factor(dat[[key]], levels = unique(dat[[key]]))
  labels <- setNames(paste0(wrap_label(dat[[key]]), "\n(n=", scales::comma(dat$n),
                            ifelse(dat$n < threshold, "; low n", ""), ")"), dat[[key]])
  long <- dat |> select(bar, monetary, nonmonetary, no_relief) |>
    pivot_longer(-bar, names_to = "outcome", values_to = "rate")
  long$outcome <- factor(long$outcome, levels = c("monetary", "nonmonetary", "no_relief"),
                         labels = names(OUTCOME_COLORS))
  long$label <- ifelse(long$rate >= .07, scales::percent(long$rate, accuracy = 1), "")
  long$ink <- ifelse(long$outcome == "Monetary relief", "white", "#1b2a41")
  ggplot(long, aes(bar, rate, fill = outcome, group = outcome)) +
    geom_col(position = position_fill(reverse = TRUE), width = .67) +
    geom_text(aes(label = label, color = ink), position = position_fill(vjust = .5, reverse = TRUE),
              size = 3.6, show.legend = FALSE) +
    coord_flip() + scale_x_discrete(labels = labels) +
    scale_y_continuous(labels = scales::label_percent(), breaks = seq(0, 1, .25),
                       limits = c(0, 1), expand = expansion(mult = c(0, 0))) +
    scale_fill_manual(values = OUTCOME_COLORS, drop = FALSE) + scale_color_identity() +
    labs(x = NULL, y = "Share of eligible complaints (100% per bar)", title = title,
         caption = "Small segments remain visible; labels are shown for shares of 7% or more.") +
    chart_theme() + guides(fill = guide_legend(ncol = 1, byrow = TRUE))
}

ui <- fluidPage(
  tags$head(tags$meta(name = "viewport", content = "width=device-width, initial-scale=1"),
            tags$style(HTML("
      :root { --ink:#1b2a41; --paper:#eef1f6; --brass:#b8842e; --teal:#3f6e64; }
      body { background:var(--paper); color:var(--ink); font-family:Arial,sans-serif; }
      .container-fluid { max-width:1600px; padding:0 28px 30px; }
      .masthead { display:flex; justify-content:space-between; align-items:center; gap:20px; padding:30px 0 24px; }
      .brand { font-family:Georgia,serif; font-size:28px; letter-spacing:-.8px; }
      .brand span { color:var(--brass); margin-right:10px; }
      .masthead small,.muted { color:#647084; line-height:1.6; }
      .eyebrow { text-transform:uppercase; letter-spacing:1.6px; font-size:10px; font-weight:bold; color:#738097; }
      .hero { padding:12px 0 24px; }
      h1,h2,h3 { font-family:Georgia,serif; color:var(--ink); }
      h1 { font-size:36px; margin:10px 0; } h3 { font-size:22px; margin:0 0 12px; }
      .compass-card,.kpi { background:#fbfcfe; border:1px solid #d8dfe8; border-radius:12px; padding:22px; margin-bottom:20px; }
      .kpi { min-height:135px; } .kpi-value { font-size:30px; font-weight:600; margin:10px 0 5px; }
      .filter-panel { background:var(--ink); color:#eef1f6; border-radius:12px; padding:23px; margin-bottom:20px; }
      .filter-panel h3 { color:white; } .filter-panel .help-block { color:#bcc8d7; font-size:12px; }
      .filter-panel .eyebrow { color:#d5b477; } .filter-panel label { font-size:12px; }
      .filter-panel .selectize-input,.filter-panel .selectize-dropdown { color:#1b2a41; }
      .filter-panel .btn { white-space:normal; } .filter-panel .form-control { color:#1b2a41; }
      .nav-tabs { border-bottom:1px solid #ccd5df; margin-bottom:24px; }
      .nav-tabs>li>a { color:#647084; padding:13px 16px; border:0; font-weight:600; }
      .nav-tabs>li.active>a,.nav-tabs>li.active>a:focus,.nav-tabs>li.active>a:hover { color:var(--teal); background:transparent; border:0; border-bottom:3px solid var(--teal); }
      .btn-primary { background:var(--ink); border-color:var(--ink); border-radius:6px; }
      .btn-primary:hover { background:var(--teal); border-color:var(--teal); }
      .notice { background:#f4eddd; border-left:3px solid var(--brass); padding:13px 16px; margin-bottom:20px; line-height:1.6; font-size:13px; }
      .badge-source { background:#e4ece8; color:#32584f; border-radius:18px; padding:8px 14px; font-size:12px; }
      .chart-scroll { max-height:690px; overflow:auto; }
      .table { font-size:12px; } .table th { color:#647084; }
      .rank-row { display:flex; align-items:center; gap:16px; padding:18px 0; border-bottom:1px solid #e2e7ed; }
      .rank-number { font-family:Georgia,serif; font-size:30px; color:var(--brass); min-width:30px; }
      .rank-name { font-size:17px; font-weight:600; flex:1; } .rank-score { font-size:25px; color:var(--teal); }
      .shiny-output-error-validation { color:#647084; padding:18px; }
      .footer-note { color:#647084; font-size:12px; margin-top:22px; line-height:1.6; }
      @media(max-width:767px) { .container-fluid { padding:0 12px 20px; } .masthead { align-items:flex-start; } h1 { font-size:29px; } .brand { font-size:23px; } .nav-tabs>li>a { padding:10px; } .kpi { min-height:100px; } }
    "))),
  div(class = "masthead", div(div(class = "brand", span("\u25c8"), "Dashboard"),
                              tags$small("Complaint outcomes.")),
      uiOutput("source_badge")),
  fluidRow(
    column(3,
           div(class = "filter-panel",
               div(class = "eyebrow", "Your data, your view"), h3("Explore the evidence"),
               selectInput("source", "Data source", c("Website reference snapshot" = "reference", "Upload CFPB CSV" = "upload")),
               conditionalPanel("input.source == 'upload'",
                                fileInput("csv", "Original complaint CSV", accept = ".csv"),
                                helpText("Spaced and dotted column names are supported. Maximum 200 MB."),
                                selectizeInput("products", "Products", choices = NULL, multiple = TRUE),
                                helpText("Credit-card products are selected initially when present. Empty means all products.")),
               selectInput("year", "Year received", c("All years" = "all")),
               selectizeInput("banks", "Compare banks / companies", choices = sort(unique(REFERENCE$bank)), multiple = TRUE,
                              options = list(placeholder = "All available banks")),
               helpText("Leave empty to include all available banks / companies."),
               numericInput("min_n", "Minimum complaints per risk for ranking", value = 20, min = 1, step = 1),
               downloadButton("download_summary", "Download filtered summary")),
           div(class = "footer-note", strong("How to read risk"), br(),
               "Risk categories are areas of concern. Complaint shares and relief rates do not estimate the chance that a customer will experience a problem.")
    ),
    column(9,
           div(class = "hero", div(class = "eyebrow", "THE CREDIT CARD COMPASS / EXPLORER"),
               h1("A clearer view of your concerns.")),
           uiOutput("data_status"),
           tabsetPanel(id = "page",
                       tabPanel("Dashboard", value = "dashboard",
                                uiOutput("dashboard_kpis"),
                                card("The complaint landscape", subtitle = "Each bar is a bank; colors show risk / concern categories in the eligible complaint records.",
                                     radioButtons("volume_scale", NULL, c("100% stacked" = "share"), selected = "share", inline = TRUE),
                                     div(class = "chart-scroll", uiOutput("landscape_ui"))),
                                fluidRow(column(6, card("Response outcomes by bank", subtitle = "Three outcomes fill each bar to 100%; all risk categories combined.", plotOutput("overall_relief", height = "430px"))),
                                         column(6, card("Risk category mix", subtitle = "Share of eligible complaint records in the current selection.", plotOutput("concern_mix", height = "350px")))),
                                card("Across the years",
                                     uiOutput("trend_note"), plotOutput("annual", height = "280px"))),
                       tabPanel("Bank overview", value = "bank",
                                card("Inside a bank", selectInput("profile_bank", "Choose a bank", sort(unique(REFERENCE$bank)))),
                                uiOutput("bank_kpis"),
                                card("Outcomes by risk category", subtitle = "Within each category, the response shares sum to 100%.", plotOutput("bank_outcomes", height = "440px")),
                                card("Where complaints concentrate", plotOutput("bank_volume", height = "350px"))
                                ),
                       tabPanel("Risk comparison", value = "risk",
                                card("Compare what matters", fluidRow(
                                  column(6, selectInput("risk", "Risk / concern category", CONCERNS, selected = "Billing/Disputes")),
                                  column(6, selectInput("metric", "Measure", c("Response outcomes (100% stacked)" = "outcomes", "Total relief rate" = "total", "Monetary relief rate" = "monetary",
                                                                               "Non-monetary relief rate" = "nonmonetary", "No recorded relief" = "no_relief", "Complaint count" = "n"))))),
                                uiOutput("risk_definition"),
                                card("Bank comparison", div(class = "chart-scroll", uiOutput("risk_plot_ui")),
                                     downloadButton("download_risk", "Download this chart (PDF)")),
                                card("Compare all risk categories", subtitle = "Total relief rate by bank and category. Blank cells mean no eligible records, not a zero relief rate.",
                                     div(class = "chart-scroll", uiOutput("heatmap_ui"))))
           )
    )
  )
)

server <- function(input, output, session) {
  uploaded <- reactive({
    req(input$csv)
    result <- tryCatch(prepare_records(read.csv(input$csv$datapath, stringsAsFactors = FALSE,
                                                check.names = FALSE, fileEncoding = "UTF-8-BOM")), error = function(e) e)
    validate(need(!inherits(result, "error"), if (inherits(result, "error")) conditionMessage(result) else ""))
    result
  })
  observeEvent(list(input$source, input$csv), {
    if (input$source == "reference") {
      updateSelectInput(session, "year", choices = c("All years" = "all"), selected = "all")
      updateSelectizeInput(session, "banks", choices = sort(unique(REFERENCE$bank)), selected = character(0), server = TRUE)
    } else if (!is.null(input$csv)) {
      dat <- uploaded()
      years <- sort(unique(dat$year[!is.na(dat$year)]), decreasing = TRUE)
      choices <- c("All years" = "all", setNames(as.character(years), as.character(years)))
      if (anyNA(dat$year)) choices <- c(choices, "Missing / unreadable dates" = "unknown")
      updateSelectInput(session, "year", choices = choices, selected = "all")
      updateSelectizeInput(session, "banks", choices = sort(unique(dat$bank)), selected = character(0), server = TRUE)
      products <- sort(unique(dat$product)); defaults <- products[grepl("credit card", products, ignore.case = TRUE)]
      updateSelectizeInput(session, "products", choices = products, selected = defaults, server = TRUE)
    }
  }, ignoreNULL = FALSE)
  period <- reactive({
    if (input$source == "reference" || is.null(input$year) || input$year == "all") return("All years")
    if (input$year == "unknown") return("Missing dates")
    input$year
  })
  base_records <- reactive({
    req(input$source == "upload")
    dat <- uploaded()
    if (length(input$banks)) dat <- filter(dat, bank %in% input$banks)
    if (length(input$products)) dat <- filter(dat, product %in% input$products)
    dat
  })
  filtered_records <- reactive({
    dat <- base_records()
    if (identical(input$year, "unknown")) dat <- filter(dat, is.na(year))
    else if (!is.null(input$year) && input$year != "all") dat <- filter(dat, year == as.integer(input$year))
    dat
  })
  stats <- reactive({
    if (input$source == "reference") {
      dat <- REFERENCE
      if (length(input$banks)) dat <- filter(dat, bank %in% input$banks)
    } else dat <- aggregate_records(filtered_records())
    dat
  })
  checked_stats <- reactive({
    dat <- stats()
    validate(need(nrow(dat) > 0, "No eligible complaint records match this selection. Try another year, product or bank."))
    dat
  })
  min_n <- reactive({
    if (is.null(input$min_n) || !is.finite(input$min_n)) return(20)
    max(1, ceiling(input$min_n))
  })
  totals <- reactive(bank_totals(checked_stats()))
  observeEvent(stats(), {
    banks <- sort(unique(stats()$bank))
    current <- isolate(input$profile_bank)
    selected <- if (length(current) && current %in% banks) current else if (length(banks)) banks[1] else character(0)
    updateSelectInput(session, "profile_bank", choices = banks, selected = selected)
  })
  output$source_badge <- renderUI({
    div(class = "badge-source", if (input$source == "reference") "Website snapshot \u00b7 4 banks" else "Uploaded CSV")
  })
  output$data_status <- renderUI({
    if (input$source == "reference") return(notice("Reference snapshot from your website: 83,902 records. Upload the original CSV to explore individual years."))
    if (is.null(input$csv)) return(notice("Choose a CSV in the left panel to begin. Required: Company, Issue, Company response to consumer, Date received."))
    dat <- filtered_records()
    missing_dates <- sum(is.na(base_records()$date))
    notice(paste0(period(), " | ", scales::comma(nrow(dat)), " uploaded records in view; ",
                  scales::comma(sum(dat$eligible)), " eligible for relief calculations. Excluded: ",
                  scales::comma(sum(dat$response == "In progress")), " in progress and ",
                  scales::comma(sum(dat$response == "(Missing response)")), " without a response. ",
                  scales::comma(missing_dates), " selected-bank/product records have unreadable or missing dates (kept in All years)."))
  })
  output$dashboard_kpis <- renderUI({
    d <- checked_stats()
    fluidRow(kpi("Eligible complaints", scales::comma(sum(d$n)), period()),
             kpi("Banks / companies", n_distinct(d$bank), "In the current view"),
             kpi("Total relief", scales::percent(weighted.mean(d$total, d$n), accuracy = 0.1), "Monetary + non-monetary"),
             kpi("Risk categories", n_distinct(d$concern), "With eligible records"))
  })
  landscape_plot <- reactive({
    d <- checked_stats(); order <- totals() |> arrange(n, bank) |> pull(bank)
    d$bank <- factor(d$bank, levels = order)
    d$concern <- factor(d$concern, levels = CONCERNS)
    d <- d |> group_by(bank) |> mutate(share = n / sum(n)) |> ungroup()
    d$label <- ifelse(d$share >= .07, scales::percent(d$share, accuracy = 1), "")
    d$ink <- ifelse(d$concern %in% c("Account Management", "Billing/Disputes", "Payments"), "white", "#1b2a41")
    share <- identical(input$volume_scale, "share")
    d$value <- if (share) d$share else d$n
    ggplot(d, aes(bank, value, fill = concern, group = concern)) +
      geom_col(position = if (share) position_fill(reverse = TRUE) else position_stack(reverse = TRUE), width = 0.68) +
      (if (share) geom_text(aes(label = label, color = ink), position = position_fill(vjust = .5, reverse = TRUE), size = 3.4, show.legend = FALSE)) +
      coord_flip() + scale_x_discrete(labels = wrap_label) +
      scale_y_continuous(labels = if (share) scales::label_percent() else scales::label_comma(),
                         limits = if (share) c(0, 1) else NULL,
                         breaks = if (share) seq(0, 1, .25) else waiver(),
                         expand = expansion(mult = if (share) c(0, 0) else c(0, .05))) +
      scale_fill_manual(values = CONCERN_COLORS, breaks = CONCERNS, drop = FALSE) + scale_color_identity() +
      labs(x = NULL, y = if (share) "Share within bank" else "Eligible complaints") + chart_theme() + guides(fill = guide_legend(ncol = 3))
  })
  output$landscape_ui <- renderUI({ plotOutput("landscape", height = paste0(max(370, n_distinct(checked_stats()$bank) * 52 + 150), "px")) })
  output$landscape <- renderPlot(landscape_plot(), res = 100)
  output$overall_relief <- renderPlot({
    outcome_chart(totals(), "bank", min_n())
  })
  output$concern_mix <- renderPlot({
    d <- checked_stats() |> group_by(concern) |> summarise(n = sum(n), .groups = "drop") |> mutate(share = n / sum(n))
    ggplot(d, aes(reorder(concern, share), share, fill = concern)) + geom_col(width = .65, show.legend = FALSE) + coord_flip() +
      scale_y_continuous(labels = scales::label_percent()) + scale_fill_manual(values = CONCERN_COLORS) +
      labs(x = NULL, y = "Share of eligible complaints") + chart_theme()
  })
  output$trend_note <- renderUI({
    if (input$source == "reference") p(class = "muted", "The supplied website has aggregate statistics only. Upload a dated CSV to see this chart.")
    else p(class = "muted", "Eligible complaint records only. Missing dates are omitted. A partial year is not directly comparable with a full year.")
  })
  output$annual <- renderPlot({
    validate(need(input$source == "upload", "Annual data becomes available after a CSV upload."))
    d <- base_records() |> filter(eligible, !is.na(year)) |> count(year, name = "n")
    validate(need(nrow(d) > 0, "No dated eligible complaints in this selection."))
    d <- tibble(year = seq.int(min(d$year), max(d$year))) |> left_join(d, by = "year") |>
      mutate(n = coalesce(n, 0L), selected = as.character(year) == input$year)
    ggplot(d, aes(factor(year), n, fill = selected)) + geom_col(width = .6) +
      scale_fill_manual(values = c("FALSE" = "#3f6e64", "TRUE" = "#b8842e"), guide = "none") +
      scale_y_continuous(labels = scales::label_comma()) + labs(x = "Year received", y = "Eligible complaints") + chart_theme()
  })
  profile <- reactive({
    req(input$profile_bank)
    d <- checked_stats() |> filter(bank == input$profile_bank)
    validate(need(nrow(d) > 0, "This bank has no eligible complaints in this selection.")); d
  })
  output$bank_kpis <- renderUI({
    d <- profile()
    fluidRow(kpi("Eligible complaints", scales::comma(sum(d$n)), input$profile_bank),
             kpi("Total relief", scales::percent(weighted.mean(d$total, d$n), .1), period()),
             kpi("Monetary relief", scales::percent(weighted.mean(d$monetary, d$n), .1)),
             kpi("Non-monetary relief", scales::percent(weighted.mean(d$nonmonetary, d$n), .1)))
  })
  output$bank_outcomes <- renderPlot({
    outcome_chart(profile(), "concern", min_n())
  })
  output$bank_volume <- renderPlot({
    d <- profile()
    ggplot(d, aes(reorder(concern, n), n, fill = concern)) + geom_col(show.legend = FALSE, width = .65) +
      coord_flip() + scale_fill_manual(values = CONCERN_COLORS) + scale_y_continuous(labels = scales::label_comma()) +
      labs(x = NULL, y = "Eligible complaints") + chart_theme()
  })
  display_table <- function(d) {
    d |> transmute(Bank = bank, Risk = concern, Complaints = n,
                   `Monetary relief` = scales::percent(monetary, .1),
                   `Non-monetary relief` = scales::percent(nonmonetary, .1),
                   `Total relief` = scales::percent(total, .1),
                   `Sample size` = ifelse(n < min_n(), "Low n", "Meets threshold"))
  }
  output$bank_table <- renderTable(display_table(profile()), striped = TRUE, bordered = FALSE)
  risk_stats <- reactive({
    d <- checked_stats() |> filter(concern == input$risk)
    validate(need(nrow(d) > 0, "No eligible records for this risk category in the current selection.")); d
  })
  metric_label <- reactive(switch(input$metric, total = "Total relief rate", monetary = "Monetary relief rate",
                                  nonmonetary = "Non-monetary relief rate", no_relief = "No recorded relief", n = "Eligible complaint count"))
  output$risk_definition <- renderUI({
    text <- if (input$metric == "outcomes") "Each bank's bar totals 100% of eligible complaints within this risk category: monetary relief (blue), non-monetary relief (amber), and no recorded relief (gray). The count n is shown beside the bank name."
    else if (input$metric == "n") "Counts show reporting volume, not a per-customer risk rate."
    else if (input$metric == "no_relief") "No recorded relief is the share of eligible complaints without either relief label. A higher value does not establish unfair treatment or a higher chance of experiencing a problem."
    else "Higher values mean relief was recorded more often among eligible complaints in this category. They do not measure the probability of experiencing this problem."
    notice(strong(paste0(input$risk, ": ")), text, " Low-n groups remain visible and are labeled.")
  })
  risk_chart <- reactive({
    if (identical(input$metric, "outcomes")) {
      return(outcome_chart(risk_stats(), "bank", min_n(), paste(input$risk, "|", period())))
    }
    d <- risk_stats(); d$value <- d[[input$metric]]; d$low <- d$n < min_n()
    d$label <- paste0(if (input$metric == "n") scales::comma(d$value) else scales::percent(d$value, .1),
                      "  |  n=", scales::comma(d$n), ifelse(d$low, " (low n)", ""))
    ggplot(d, aes(reorder(bank, value), value, fill = low)) + geom_col(width = .62) + coord_flip(clip = "off") +
      geom_text(aes(label = label), hjust = -.05, size = 3.6) + scale_x_discrete(labels = wrap_label) +
      scale_fill_manual(values = c("FALSE" = "#3f6e64", "TRUE" = "#a2acb9"), guide = "none") +
      scale_y_continuous(labels = if (input$metric == "n") scales::label_comma() else scales::label_percent(),
                         expand = expansion(mult = c(0, .48)), limits = if (input$metric == "n") NULL else c(0, 1)) +
      labs(x = NULL, y = metric_label(), title = paste(input$risk, "|", period())) + chart_theme()
  })
  output$risk_plot_ui <- renderUI({ plotOutput("risk_plot", height = paste0(max(350, nrow(risk_stats()) * 60 + 120), "px")) })
  output$risk_plot <- renderPlot(risk_chart(), res = 100)
  output$heatmap_ui <- renderUI({ plotOutput("heatmap", height = paste0(max(400, n_distinct(checked_stats()$bank) * 55 + 120), "px")) })
  output$heatmap <- renderPlot({
    d <- checked_stats()
    d$label <- paste0(scales::percent(d$total, 1), ifelse(d$n < min_n(), "*", ""))
    ggplot(d, aes(factor(concern, levels = CONCERNS), bank, fill = total)) + geom_tile(color = "#fbfcfe", linewidth = 2) +
      geom_text(aes(label = label), size = 3.5) +
      scale_fill_gradient(low = "#eff3f0", high = "#72aa96", limits = c(0, 1), labels = scales::label_percent()) +
      scale_x_discrete(labels = function(x) wrap_label(x, 16), drop = FALSE) + scale_y_discrete(labels = wrap_label) +
      labs(x = NULL, y = NULL, caption = "* Below the minimum sample-size threshold") + chart_theme() +
      theme(panel.grid = element_blank())
  })
  output$risk_table <- renderTable(display_table(risk_stats()), striped = TRUE)
  observeEvent(input$apply_quiz, {
    answers <- list(input$q1, input$q2, input$q3, input$q4)
    if (any(vapply(answers, function(x) is.null(x) || !length(x) || identical(x, ""), logical(1)))) {
      showNotification("Please answer all four questions first.", type = "message"); return()
    }
    w <- do.call(quiz_weights, answers)
    for (i in seq_along(CONCERNS)) updateSliderInput(session, paste0("w", i), value = w[CONCERNS[i]])
    showNotification("Priorities updated from your answers. You can adjust any slider.", type = "message")
  })
  observeEvent(input$reset_weights, {
    for (i in seq_along(CONCERNS)) updateSliderInput(session, paste0("w", i), value = 1)
  })
  weights <- reactive({
    values <- vapply(seq_along(CONCERNS), function(i) {
      x <- input[[paste0("w", i)]]; if (is.null(x)) 1 else x
    }, numeric(1))
    setNames(values, CONCERNS)
  })
  ranked <- reactive(rank_banks(checked_stats(), weights(), min_n()))
  output$ranking <- renderUI({
    d <- ranked(); w <- weights()
    if (!any(w > 0)) return(notice("Select at least one category by setting its weight above zero."))
    if (!nrow(d)) return(notice("No banks have observations in the weighted categories."))
    good <- d |> filter(eligible); bad <- d |> filter(!eligible)
    tagList(
      p(class = "muted", paste0("Weighted by ", sum(w > 0), " selected categories | ", period(), ". All rates below are weighted relief scores.")),
      if (!nrow(good)) notice("No bank meets the sample-size threshold in every selected category. Broaden the year selection, lower the threshold, or set an unavailable category's weight to zero."),
      lapply(seq_len(nrow(good)), function(i) div(class = "rank-row",
                                                  div(class = "rank-number", sprintf("%02d", i)),
                                                  div(class = "rank-name", good$bank[i], div(class = "muted", style = "font-size:12px;font-weight:normal;", paste(scales::comma(good$n[i]), "complaints across weighted categories"))),
                                                  div(class = "rank-score", scales::percent(good$score[i], .1)))),
      if (nrow(bad)) notice(paste("Not ranked because of missing categories or low sample sizes:", paste(bad$bank, collapse = ", ")))
    )
  })
  output$ranking_plot <- renderPlot({
    d <- ranked()
    validate(need(nrow(d) > 0, "Choose a positive weight and categories with data."))
    d <- filter(d, eligible)
    validate(need(nrow(d) > 0, "No eligible ranking with the current settings."))
    ggplot(d, aes(reorder(bank, score), score)) + geom_col(fill = "#b8842e", width = .6) + coord_flip() +
      scale_x_discrete(labels = wrap_label) + scale_y_continuous(labels = scales::label_percent(), limits = c(0, 1)) +
      labs(x = NULL, y = "Weighted relief score") + chart_theme()
  })
  output$mapping_table <- renderTable({
    data.frame(Issue = c(names(ISSUE_MAP), "All remaining / missing issues"),
               Category = c(unname(ISSUE_MAP), "Other"))
  }, striped = TRUE)
  output$download_summary <- downloadHandler(
    filename = function() paste0("compass_summary_", input$source, "_", gsub(" ", "_", period()), ".csv"),
    content = function(file) {
      checked_stats() |> mutate(source = input$source, period = period(),
                                sample_threshold = min_n(), low_n = n < min_n()) |>
        write.csv(file, row.names = FALSE)
    })
  output$download_risk <- downloadHandler(
    filename = function() paste0("compass_risk_", gsub("[^A-Za-z0-9]", "_", input$risk), ".pdf"),
    content = function(file) ggsave(file, risk_chart(), device = "pdf", width = 13,
                                    height = max(5, nrow(risk_stats()) * .6 + 2), limitsize = FALSE))
}

shinyApp(ui, server)