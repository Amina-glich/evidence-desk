"""Which measurements may be plotted together (P1-P5), and evidence checks (V1-V6).

Pure logic over reports built in memory (tests.reports), so these run on
every platform. Page texts are synthetic.
"""

from __future__ import annotations

import unittest

from desk.measurements import compare, parse_number, value_in_quote
from tests.reports import report_for


DEFINITION_QUOTE = "All accuracies use single-crop 224 evaluation on the ImageNet validation set."
PAPER_A = (
  "Evaluation protocol. " + DEFINITION_QUOTE + "\n",
  "Our single model reaches 76.3% top-1 accuracy for image classification on the ImageNet validation set.\n"
  "In the abstract we state 77.0% top-1 accuracy for image classification on the ImageNet validation set with a single model.\n"
  "An ensemble of 10 models reaches 78.1% top-1 accuracy for image classification on the ImageNet validation set.\n",
  "Training the single model for image classification on the ImageNet training set takes 3.5 days "
  "of wall-clock training time on 8 NVIDIA V100 GPUs.\n",
)
PAPER_B = (
  "Protocol: " + DEFINITION_QUOTE + "\n",
  "A single model achieves 74.9% top-1 accuracy for image classification on the ImageNet validation set.\n"
  "Its mAP@0.5 is 61.2 for image classification on the ImageNet validation set with a single model.\n",
  "Training the single model for image classification on the ImageNet training set takes 5.2 days "
  "of wall-clock training time on 8 NVIDIA V100 GPUs.\n",
)
TEN_CROP_QUOTE = "All accuracies use ten-crop 224 evaluation on the ImageNet validation set."
PAPER_B_TEN = (
  "Protocol: " + TEN_CROP_QUOTE + "\n",
  PAPER_B[1], PAPER_B[2],
)
A_77_TEN = "A ten-crop run of the single model reaches 77.4% top-1 accuracy for image classification on the ImageNet validation set."
PAPER_A_TEN = (
  PAPER_A[0] + "Ten-crop protocol: " + TEN_CROP_QUOTE + "\n",
  PAPER_A[1] + A_77_TEN + "\n", PAPER_A[2],
)
PAPER_C = (
  "Method. " + DEFINITION_QUOTE + "\n",
  "With a single model we obtain 75.5% top-1 accuracy for image classification on the ImageNet validation set.\n",
  "Training the single model for image classification on the ImageNet training set takes 2.0 days "
  "of wall-clock training time on 8 NVIDIA P100 GPUs.\n",
)

A_76 = "Our single model reaches 76.3% top-1 accuracy for image classification on the ImageNet validation set."
A_77 = "In the abstract we state 77.0% top-1 accuracy for image classification on the ImageNet validation set with a single model."
A_ENSEMBLE = "An ensemble of 10 models reaches 78.1% top-1 accuracy for image classification on the ImageNet validation set."
B_74 = "A single model achieves 74.9% top-1 accuracy for image classification on the ImageNet validation set."
B_MAP = "Its mAP@0.5 is 61.2 for image classification on the ImageNet validation set with a single model."
C_75 = "With a single model we obtain 75.5% top-1 accuracy for image classification on the ImageNet validation set."


def fields(**overrides):
  base = {
    "task": {"label": "Image classification", "as_written": "image classification"},
    "dataset": {"label": "ImageNet", "as_written": "ImageNet"},
    "split": {"label": "val", "as_written": "validation set"},
    "metric": {"label": "Top-1 accuracy", "as_written": "top-1 accuracy"},
    "metric_definition": {
      "label": "Single-crop 224", "as_written": "single-crop 224 evaluation", "page": 1, "quote": DEFINITION_QUOTE,
    },
    "variant": {"label": "Single model", "as_written": "single model"},
  }
  base.update(overrides)
  return {name: value for name, value in base.items() if value is not None}


def accuracy(value_text, quote, *, page=2, **field_overrides):
  return {
    "dimension": "results", "metric_kind": "quality", "value_text": value_text, "unit": "%",
    "evidence": [{"page": page, "quote": quote}], "fields": fields(**field_overrides),
  }


def training(value_text, quote, hardware):
  return {
    "dimension": "runtime", "metric_kind": "training_cost", "value_text": value_text, "unit": "days",
    "evidence": [{"page": 3, "quote": quote}],
    "fields": fields(
      split={"label": "train", "as_written": "training set"},
      metric={"label": "Training time", "as_written": "training time"},
      metric_definition={"label": "Wall-clock days", "as_written": "wall-clock"},
      hardware=hardware,
    ),
  }


def source(source_id, pages, *measurements):
  return report_for({}, source_id=source_id, pages=pages, measurements=list(measurements))


def statuses(comparison):
  return [(row.assessed.source_id, row.assessed.measurement.value_text, row.chart, row.reason) for row in comparison.rows]


class NumberTest(unittest.TestCase):

  def test_plain_written_numbers_only(self):
    for text, number in (("28.4", 28.4), ("50,000", 50000.0), ("91.2%", 91.2), ("3.3e18", 3.3e18), ("7", 7.0)):
      with self.subTest(text=text):
        self.assertEqual(parse_number(text), number)
    for text in ("28.4 BLEU", "1,00", "", "~28", "28.4.1", "1/2", "twenty"):
      with self.subTest(text=text):
        self.assertIsNone(parse_number(text))

  def test_value_must_be_a_whole_token_of_the_quote(self):
    self.assertTrue(value_in_quote("28.4", "a new BLEU score of 28.4."))
    self.assertTrue(value_in_quote("28.4", "BLEU of 28.4 on newstest2014"))
    self.assertTrue(value_in_quote("91.2%", "reaches 91.2% top-1"))
    for quote in ("a score of 128.4", "a score of 28.45", "a change of -28.4", "a change of +28.4", "28.4.1 release"):
      with self.subTest(quote=quote):
        self.assertFalse(value_in_quote("28.4", quote))


class PlotRulesTest(unittest.TestCase):

  def test_matching_quote_backed_values_from_two_sources_are_plotted(self):
    comparison = compare([source("S1", PAPER_A, accuracy("76.3%", A_76)), source("S2", PAPER_B, accuracy("74.9%", B_74))])
    (chart,) = comparison.charts
    self.assertEqual([(point.source_id, point.number) for point in chart.points], [("S1", 76.3), ("S2", 74.9)])
    self.assertNotIn("variant", chart.labels)
    self.assertEqual([point.variant for point in chart.points], ["Single model", "Single model"])
    self.assertEqual([(row.chart, row.reason) for row in comparison.rows], [(1, None), (1, None)])

  def test_labels_match_ignoring_case_and_spacing_but_nothing_else(self):
    s2 = source("S2", PAPER_B, accuracy("74.9%", B_74, dataset={"label": "  imagenet ", "as_written": "ImageNet"}))
    self.assertEqual(len(compare([source("S1", PAPER_A, accuracy("76.3%", A_76)), s2]).charts), 1)
    # mAP and mAP@0.5 are different metrics: never grouped.
    s1 = source("S1", PAPER_A, accuracy("76.3%", A_76, metric={"label": "mAP", "as_written": "top-1 accuracy"}))
    s2 = source("S2", PAPER_B, {**accuracy("61.2", B_MAP, metric={"label": "mAP@0.5", "as_written": "mAP@0.5"}), "unit": "points"})
    self.assertEqual(compare([s1, s2]).charts, [])

  def test_p1_one_source_is_never_plotted(self):
    comparison = compare([source("S1", PAPER_A, accuracy("76.3%", A_76))])
    self.assertEqual(comparison.charts, [])
    self.assertIn("no other source reports this exact combination", comparison.rows[0].reason)

  def test_p3_different_variants_are_plotted_as_separate_labelled_points(self):
    s1 = source("S1", PAPER_A, accuracy("78.1%", A_ENSEMBLE, variant={"label": "Ensemble of 10", "as_written": "ensemble of 10 models"}))
    comparison = compare([s1, source("S2", PAPER_B, accuracy("74.9%", B_74))])
    (chart,) = comparison.charts
    self.assertEqual([(point.source_id, point.variant, point.number) for point in chart.points], [
      ("S1", "Ensemble of 10", 78.1), ("S2", "Single model", 74.9),
    ])
    self.assertEqual([(row.chart, row.reason) for row in comparison.rows], [(1, None), (1, None)])
    # Shared labels never include the setup labels, which belong to each point.
    self.assertNotIn("variant", chart.labels)
    self.assertNotIn("metric_definition", chart.labels)
    self.assertEqual([point.definition for point in chart.points], ["Single-crop 224", "Single-crop 224"])

  def test_p3_a_source_with_two_variants_gets_two_points_and_still_needs_a_second_source(self):
    ensemble = accuracy("78.1%", A_ENSEMBLE, variant={"label": "Ensemble of 10", "as_written": "ensemble of 10 models"})
    both = source("S1", PAPER_A, accuracy("76.3%", A_76), ensemble)
    self.assertEqual(compare([both]).charts, [])
    (chart,) = compare([both, source("S2", PAPER_B, accuracy("74.9%", B_74))]).charts
    self.assertEqual([(point.source_id, point.variant) for point in chart.points], [
      ("S1", "Single model"), ("S1", "Ensemble of 10"), ("S2", "Single model"),
    ])

  def test_p3_the_same_variant_label_is_one_point_per_source_even_with_other_spacing(self):
    s1 = source("S1", PAPER_A, accuracy("76.3%", A_76), accuracy("76.3%", A_76, variant={"label": " single  MODEL ", "as_written": "single model"}))
    (chart,) = compare([s1, source("S2", PAPER_B, accuracy("74.9%", B_74))]).charts
    self.assertEqual([point.source_id for point in chart.points], ["S1", "S2"])

  def test_p3_evaluation_details_still_have_to_match_exactly(self):
    base = lambda **overrides: source("S2", PAPER_B, accuracy("74.9%", B_74, **overrides))
    s1 = source("S1", PAPER_A, accuracy("76.3%", A_76))
    mismatches = {
      "task": {"task": {"label": "Object detection", "as_written": "image classification"}},
      "dataset": {"dataset": {"label": "ImageNet-V2", "as_written": "ImageNet"}},
      "split": {"split": {"label": "test", "as_written": "validation set"}},
      "metric": {"metric": {"label": "Top-5 accuracy", "as_written": "top-1 accuracy"}},
    }
    for name, override in mismatches.items():
      with self.subTest(field=name):
        comparison = compare([s1, base(**override)])
        self.assertEqual(comparison.charts, [])
        self.assertIsNone(comparison.rows[0].chart)
        self.assertIn("no other source reports", comparison.rows[0].reason)
    # A different unit is a different quantity.
    points = {**accuracy("74.9", B_74), "unit": "points"}
    self.assertEqual(compare([s1, source("S2", PAPER_B, points)]).charts, [])

  def test_p3_different_metric_definitions_are_plotted_with_their_own_labels(self):
    ten_crop = {"label": "Ten-crop 224", "as_written": "ten-crop 224 evaluation", "page": 1, "quote": TEN_CROP_QUOTE}
    s1 = source("S1", PAPER_A, accuracy("76.3%", A_76))
    s2 = source("S2", PAPER_B_TEN, accuracy("74.9%", B_74, metric_definition=ten_crop))
    comparison = compare([s1, s2])
    (chart,) = comparison.charts
    self.assertEqual([(point.source_id, point.definition, point.number) for point in chart.points], [
      ("S1", "Single-crop 224", 76.3), ("S2", "Ten-crop 224", 74.9),
    ])
    self.assertNotIn("metric_definition", chart.labels)
    self.assertEqual([row.chart for row in comparison.rows], [1, 1])

  def test_p3_one_source_with_two_definitions_gets_two_points(self):
    ten_crop = {"label": "Ten-crop 224", "as_written": "ten-crop 224 evaluation", "page": 1, "quote": TEN_CROP_QUOTE}
    s1 = source("S1", PAPER_A_TEN, accuracy("76.3%", A_76), accuracy("77.4%", A_77_TEN, metric_definition=ten_crop))
    (chart,) = compare([s1, source("S2", PAPER_B, accuracy("74.9%", B_74))]).charts
    self.assertEqual([(point.source_id, point.definition) for point in chart.points], [
      ("S1", "Single-crop 224"), ("S1", "Ten-crop 224"), ("S2", "Single-crop 224"),
    ])

  def test_p4_an_unknown_metric_definition_still_blocks_even_beside_a_different_known_one(self):
    ten_crop = {"label": "Ten-crop 224", "as_written": "ten-crop 224 evaluation", "page": 1, "quote": TEN_CROP_QUOTE}
    unknown = {"unknown": "The paper does not say how the image is cropped."}
    s1 = source("S1", PAPER_A, accuracy("76.3%", A_76, metric_definition=unknown))
    s2 = source("S2", PAPER_B_TEN, accuracy("74.9%", B_74, metric_definition=ten_crop))
    comparison = compare([s1, s2])
    self.assertEqual(comparison.charts, [])
    self.assertIn("metric definition unknown", comparison.rows[0].reason)

  def test_p5_conflicting_values_for_the_same_definition_and_variant_still_block(self):
    s1 = source("S1", PAPER_A, accuracy("76.3%", A_76), accuracy("77.0%", A_77))
    comparison = compare([s1, source("S2", PAPER_B, accuracy("74.9%", B_74))])
    self.assertEqual(comparison.charts, [])
    self.assertIn("conflicting values", comparison.rows[0].reason)

  def test_p2_a_failed_quotation_still_blocks_even_when_setups_differ(self):
    ten_crop = {"label": "Ten-crop 224", "as_written": "ten-crop 224 evaluation", "page": 1, "quote": TEN_CROP_QUOTE}
    bad = accuracy("74.9%", "A single model gets 74.9% on something not in the paper.", metric_definition=ten_crop)
    comparison = compare([source("S1", PAPER_A, accuracy("76.3%", A_76)), source("S2", PAPER_B_TEN, bad)])
    self.assertEqual(comparison.charts, [])
    self.assertIn("quotation did not verify", comparison.rows[1].reason)

  def test_p3_a_missing_crop_method_blocks_the_chart_even_when_the_variants_differ(self):
    # The reviewed case: one paper's crop method is not stated, the variants differ.
    unknown = {"unknown": "The paper does not say how the image is cropped for this result."}
    s1 = source("S1", PAPER_A, accuracy("78.1%", A_ENSEMBLE, variant={"label": "Distillation", "as_written": "ensemble of 10 models"}))
    s2 = source("S2", PAPER_B, accuracy("74.9%", B_74, metric_definition=unknown, variant={"label": "Training recipe A1", "as_written": "single model"}))
    comparison = compare([s1, s2])
    self.assertEqual(comparison.charts, [])
    self.assertIn("metric definition unknown (The paper does not say how the image is cropped for this result)", comparison.rows[1].reason)
    self.assertIsNone(comparison.rows[1].chart)
    # The other paper's value is not plotted against a value whose crop is unknown.
    self.assertIsNone(comparison.rows[0].chart)
    self.assertIn("no other source", comparison.rows[0].reason)

  def test_p4_an_unknown_variant_blocks_the_chart(self):
    s2 = source("S2", PAPER_B, accuracy("74.9%", B_74, variant={"unknown": "The paper does not name the training recipe."}))
    comparison = compare([source("S1", PAPER_A, accuracy("76.3%", A_76)), s2])
    self.assertEqual(comparison.charts, [])
    self.assertIn("model variant unknown", comparison.rows[1].reason)

  def test_p3_speed_and_cost_need_the_same_hardware(self):
    v100 = {"label": "8x NVIDIA V100", "as_written": "8 NVIDIA V100 GPUs"}
    p100 = {"label": "8x NVIDIA P100", "as_written": "8 NVIDIA P100 GPUs"}
    a = source("S1", PAPER_A, training("3.5", PAPER_A[2].strip(), v100))
    b = source("S2", PAPER_B, training("5.2", PAPER_B[2].strip(), v100))
    c = source("S3", PAPER_C, training("2.0", PAPER_C[2].strip(), p100))
    comparison = compare([a, b, c])
    (chart,) = comparison.charts
    self.assertEqual([point.source_id for point in chart.points], ["S1", "S2"])
    self.assertEqual(chart.labels["hardware"], "8x NVIDIA V100")
    self.assertIn("hardware differs (“8x NVIDIA P100” vs “8x NVIDIA V100”)", comparison.rows[2].reason)

  def test_p4_an_unknown_field_is_never_inferred(self):
    s1 = source("S1", PAPER_A, accuracy("76.3%", A_76, metric_definition={"unknown": "The evaluation crop is not stated."}))
    comparison = compare([s1, source("S2", PAPER_B, accuracy("74.9%", B_74))])
    self.assertEqual(comparison.charts, [])
    self.assertEqual(
      comparison.rows[0].reason,
      "Not compared: metric definition unknown (The evaluation crop is not stated).",
    )

  def test_p5_conflicting_values_from_one_source_are_not_plotted(self):
    s1 = source("S1", PAPER_A, accuracy("76.3%", A_76), accuracy("77.0%", A_77))
    comparison = compare([s1, source("S2", PAPER_B, accuracy("74.9%", B_74))])
    self.assertEqual(comparison.charts, [])
    for row in comparison.rows[:2]:
      self.assertIn("this source reports conflicting values for the same combination (76.3%, 77.0%)", row.reason)
    self.assertIn("(S1) is excluded because of conflicting or contradicted values", comparison.rows[2].reason)

  def test_p5_other_sources_can_still_be_plotted_without_the_conflicting_one(self):
    s1 = source("S1", PAPER_A, accuracy("76.3%", A_76), accuracy("77.0%", A_77))
    comparison = compare([s1, source("S2", PAPER_B, accuracy("74.9%", B_74)), source("S3", PAPER_C, accuracy("75.5%", C_75))])
    (chart,) = comparison.charts
    self.assertEqual([point.source_id for point in chart.points], ["S2", "S3"])
    self.assertIsNone(comparison.rows[0].chart)
    self.assertIn("conflicting values", comparison.rows[0].reason)

  def test_the_same_value_reported_twice_is_not_a_conflict(self):
    s1 = source("S1", PAPER_A, accuracy("76.3%", A_76), accuracy("76.3%", A_76))
    comparison = compare([s1, source("S2", PAPER_B, accuracy("74.9%", B_74))])
    (chart,) = comparison.charts
    self.assertEqual(len(chart.points), 2)
    self.assertEqual([row.chart for row in comparison.rows], [1, 1, 1])

  def test_different_metric_kinds_are_never_grouped(self):
    s2 = source("S2", PAPER_B, {**accuracy("74.9%", B_74), "metric_kind": "other"})
    self.assertEqual(compare([source("S1", PAPER_A, accuracy("76.3%", A_76)), s2]).charts, [])


class EvidenceChecksTest(unittest.TestCase):

  def reason(self, measurement, pages=PAPER_A):
    (row,) = compare([source("S1", pages, measurement)]).rows
    return row.reason

  def test_v1_unverified_quotations_block_plotting(self):
    self.assertIn("the value's quotation did not verify (p.2 not found", self.reason(accuracy("76.3%", "Our single model reaches 76.3% on everything.")))
    bad_definition = {"label": "Single-crop 224", "as_written": "single-crop", "page": 2, "quote": DEFINITION_QUOTE}
    self.assertIn(
      "the metric definition quotation did not verify (p.2 found on a different page",
      self.reason(accuracy("76.3%", A_76, metric_definition=bad_definition)),
    )
    unavailable = report_for({}, source_id="S1", pages=PAPER_A, measurements=[accuracy("76.3%", A_76)], source_available=False)
    self.assertIn("source text unavailable", compare([unavailable]).rows[0].reason)

  def test_v2_the_value_must_be_a_plain_number_in_its_quotation(self):
    self.assertIn('"76.4%" does not appear in its quotation', self.reason(accuracy("76.4%", A_76)))
    self.assertIn('"76.3 percent" is not a plain written number', self.reason({**accuracy("76.3 percent", A_76), "unit": "percent"}))

  def test_v3_field_words_must_be_in_their_quotation(self):
    reason = self.reason(accuracy("76.3%", A_76, dataset={"label": "ImageNet", "as_written": "ImageNet-1k"}))
    self.assertIn('the dataset words "ImageNet-1k" are not in its quotation', reason)

  def test_v4_percent_values_need_the_percent_unit(self):
    self.assertIn('does not match the unit "points"', self.reason({**accuracy("76.3%", A_76), "unit": "points"}))
    self.assertIn('"61.2" does not match the unit "%"', self.reason(accuracy("61.2", B_MAP), pages=PAPER_B))

  def test_v5_differences_and_ratios_are_not_measurements(self):
    for value_text in ("+1.6", "-0.5", "−0.5", "9.3x", "9.3×", "2 times"):
      with self.subTest(value_text=value_text):
        reason = self.reason({**accuracy(value_text, A_76), "unit": "points"})
        self.assertIn("a difference or ratio, not a measured value", reason)

  def test_v6_speed_and_cost_must_name_their_hardware(self):
    measurement = training("3.5", PAPER_A[2].strip(), None)
    self.assertIn("speed and training-cost values must name their hardware", self.reason(measurement))


# A synthetic stand-in for the S1/S2 audit: the same benchmarks, numbers and
# protocol differences as the Transformer and ConvS2S papers, in made-up text.
S1_PAGES = (
  "On the WMT 2014 English-to-French translation task, our model establishes a single-model "
  "BLEU score of 41.8 after training for 3.5 days on eight GPUs.\n",
  "On the WMT 2014 English-to-German translation task, the big transformer model establishes a "
  "new BLEU score of 28.4 on newstest2014.\nOn the WMT 2014 English-to-French translation task, "
  "our big model achieves a BLEU score of 41.0 on newstest2014.\n",
  "The big Transformer models were trained for 3.5 days of training on 8 NVIDIA P100 GPUs for "
  "the WMT 2014 English-to-French translation task on its training set.\n",
)
S2_PAGES = (
  "We report case-sensitive tokenized BLEU on newstest2014 for the WMT'14 English-German "
  "translation task, averaged over three runs.\n",
  "WMT'14 English-German translation: ConvS2S averaged over three runs scores 25.16 BLEU on "
  "newstest2014.\nAn ensemble of 10 models scores 26.43 BLEU on newstest2014 for WMT'14 "
  "English-German translation.\n",
  "For the WMT'14 English-French translation task our best single run reaches 40.70 BLEU on "
  "newstest2014, while averaged over three runs the model scores 40.51 BLEU on newstest2014.\n",
  "Training ConvS2S for the WMT'14 English-French translation task on its training set took "
  "about 37 days of training with 8 NVIDIA M40 GPUs.\n",
)
UNKNOWN_BLEU = {"unknown": "The BLEU variant is not stated in the recorded evidence."}
TOKENIZED_BLEU = {
  "label": "Case-sensitive tokenized BLEU", "as_written": "case-sensitive tokenized BLEU", "page": 1,
  "quote": "We report case-sensitive tokenized BLEU on newstest2014",
}


def bleu(value_text, page, quote, dataset, as_written_dataset, definition, variant):
  return {
    "dimension": "results", "metric_kind": "quality", "value_text": value_text, "unit": "BLEU",
    "evidence": [{"page": page, "quote": quote}],
    "fields": {
      "task": {"label": "Machine translation", "as_written": "translation"},
      "dataset": {"label": dataset, "as_written": as_written_dataset},
      "split": {"label": "newstest2014", "as_written": "newstest2014"} if "newstest2014" in quote
      else {"unknown": "The test set is not named in this quotation."},
      "metric": {"label": "BLEU", "as_written": "BLEU"},
      "metric_definition": definition,
      "variant": variant,
    },
  }


def train_days(value_text, page, quote, as_written_dataset, variant, hardware):
  return {
    "dimension": "runtime", "metric_kind": "training_cost", "value_text": value_text, "unit": "days",
    "evidence": [{"page": page, "quote": quote}],
    "fields": {
      "task": {"label": "Machine translation", "as_written": "translation task"},
      "dataset": {"label": "WMT14 En-Fr", "as_written": as_written_dataset},
      "split": {"label": "train", "as_written": "training set"},
      "metric": {"label": "Training time", "as_written": "days of training"},
      "metric_definition": {"label": "Days of training", "as_written": "days of training"},
      "variant": variant,
      "hardware": hardware,
    },
  }


def audit_reports():
  """S1 and S2 as recorded in the audit, with every compatibility field."""
  en_de_s1 = "On the WMT 2014 English-to-German translation task, the big transformer model establishes a new BLEU score of 28.4 on newstest2014."
  s1 = source(
    "S1", S1_PAGES,
    bleu("28.4", 2, en_de_s1, "WMT14 En-De", "WMT 2014 English-to-German", UNKNOWN_BLEU,
         {"label": "Big Transformer, single model", "as_written": "big transformer model"}),
    bleu("41.8", 1, S1_PAGES[0].strip(), "WMT14 En-Fr", "WMT 2014 English-to-French", UNKNOWN_BLEU,
         {"label": "Big Transformer, single model", "as_written": "single-model"}),
    bleu("41.0", 2, "On the WMT 2014 English-to-French translation task, our big model achieves a BLEU score of 41.0 on newstest2014.",
         "WMT14 En-Fr", "WMT 2014 English-to-French", UNKNOWN_BLEU,
         {"label": "Big Transformer, single model", "as_written": "big model"}),
    train_days("3.5", 3, S1_PAGES[2].strip(), "WMT 2014 English-to-French",
               {"label": "Big Transformer", "as_written": "big Transformer models"},
               {"label": "8x NVIDIA P100", "as_written": "8 NVIDIA P100 GPUs"}),
  )
  s2 = source(
    "S2", S2_PAGES,
    bleu("25.16", 2, "WMT'14 English-German translation: ConvS2S averaged over three runs scores 25.16 BLEU on newstest2014.",
         "WMT14 En-De", "WMT'14 English-German", TOKENIZED_BLEU,
         {"label": "ConvS2S, average of three runs", "as_written": "averaged over three runs"}),
    bleu("26.43", 2, "An ensemble of 10 models scores 26.43 BLEU on newstest2014 for WMT'14 English-German translation.",
         "WMT14 En-De", "WMT'14 English-German", TOKENIZED_BLEU,
         {"label": "ConvS2S, ensemble of 10", "as_written": "ensemble of 10 models"}),
    bleu("40.70", 3, S2_PAGES[2].strip(), "WMT14 En-Fr", "WMT'14 English-French", TOKENIZED_BLEU,
         {"label": "ConvS2S, best single run", "as_written": "best single run"}),
    bleu("40.51", 3, S2_PAGES[2].strip(), "WMT14 En-Fr", "WMT'14 English-French", TOKENIZED_BLEU,
         {"label": "ConvS2S, average of three runs", "as_written": "averaged over three runs"}),
    train_days("37", 4, S2_PAGES[3].strip(), "WMT'14 English-French",
               {"label": "ConvS2S", "as_written": "ConvS2S"},
               {"label": "8x NVIDIA M40", "as_written": "8 NVIDIA M40 GPUs"}),
  )
  return [s1, s2]


class AuditRegressionTest(unittest.TestCase):
  """Same benchmark is not comparable: the S1/S2 audit must plot nothing."""

  def setUp(self):
    self.comparison = compare(audit_reports())
    self.reasons = {(row.assessed.source_id, row.assessed.measurement.value_text): row.reason for row in self.comparison.rows}

  def test_nothing_is_plotted_and_every_value_says_why(self):
    self.assertEqual(self.comparison.charts, [])
    self.assertEqual(len(self.comparison.rows), 9)
    for key, reason in self.reasons.items():
      with self.subTest(value=key):
        self.assertTrue(reason and reason.startswith("Not compared: "), reason)

  def test_reasons_match_the_audit(self):
    for value in ("28.4", "41.8", "41.0"):
      self.assertIn("metric definition unknown (The BLEU variant is not stated", self.reasons[("S1", value)])
    for value in ("25.16", "26.43", "40.70", "40.51"):
      self.assertIn("no other source reports this exact combination", self.reasons[("S2", value)])
    for key, other in ((("S1", "3.5"), "S2"), (("S2", "37"), "S1")):
      self.assertIn(f"{other} reports the same task, dataset, split and metric", self.reasons[key])
      self.assertNotIn("model variant differs", self.reasons[key])
      self.assertIn("hardware differs", self.reasons[key])

  def test_with_a_known_definition_the_variants_would_be_separate_labelled_points(self):
    # Hypothetically suppose S1 did state the same BLEU definition. The real audit
    # still plots nothing: S1's definition is unknown and the hardware differs.
    known = {"label": "Case-sensitive tokenized BLEU", "as_written": "BLEU"}
    en_de_s1 = "On the WMT 2014 English-to-German translation task, the big transformer model establishes a new BLEU score of 28.4 on newstest2014."
    s1 = source("S1", S1_PAGES, bleu(
      "28.4", 2, en_de_s1, "WMT14 En-De", "WMT 2014 English-to-German", known,
      {"label": "Big Transformer, single model", "as_written": "big transformer model"},
    ))
    s2 = source("S2", S2_PAGES, bleu(
      "25.16", 2, "WMT'14 English-German translation: ConvS2S averaged over three runs scores 25.16 BLEU on newstest2014.",
      "WMT14 En-De", "WMT'14 English-German", TOKENIZED_BLEU,
      {"label": "ConvS2S, average of three runs", "as_written": "averaged over three runs"},
    ))
    comparison = compare([s1, s2])
    (chart,) = comparison.charts
    self.assertEqual([(point.source_id, point.variant) for point in chart.points], [
      ("S1", "Big Transformer, single model"), ("S2", "ConvS2S, average of three runs"),
    ])


class ReviewRegressionTest(unittest.TestCase):
  """Findings of the v0.5.0 review: each must stay fixed."""

  def test_a_conflict_is_found_even_when_the_other_value_has_a_problem(self):
    # The reviewed case: 77.0% has a typo in a field, which used to let 76.3% be plotted.
    typo = accuracy("77.0%", A_77, dataset={"label": "ImageNet", "as_written": "ImageNet-1k"})
    comparison = compare([source("S1", PAPER_A, accuracy("76.3%", A_76), typo), source("S2", PAPER_B, accuracy("74.9%", B_74))])
    self.assertEqual(comparison.charts, [])
    plotted, other, partner = comparison.rows
    self.assertIsNone(plotted.chart)
    self.assertIn("this source reports conflicting values for the same combination (76.3%, 77.0%)", plotted.reason)
    self.assertIn('the dataset words "ImageNet-1k" are not in its quotation', other.reason)
    self.assertIn("conflicting values", other.reason)
    self.assertIn("(S1) is excluded because of conflicting or contradicted values", partner.reason)

  def test_other_sources_are_still_plotted_without_the_conflicting_one(self):
    typo = accuracy("77.0%", A_77, dataset={"label": "ImageNet", "as_written": "ImageNet-1k"})
    comparison = compare([
      source("S1", PAPER_A, accuracy("76.3%", A_76), typo),
      source("S2", PAPER_B, accuracy("74.9%", B_74)), source("S3", PAPER_C, accuracy("75.5%", C_75)),
    ])
    (chart,) = comparison.charts
    self.assertEqual([point.source_id for point in chart.points], ["S2", "S3"])
    self.assertEqual([row.chart for row in comparison.rows], [None, None, 1, 1])

  def test_an_unknown_field_counts_as_possibly_the_same_combination(self):
    unknown_variant = accuracy("77.0%", A_77, variant={"unknown": "Not stated next to this value."})
    comparison = compare([source("S1", PAPER_A, accuracy("76.3%", A_76), unknown_variant), source("S2", PAPER_B, accuracy("74.9%", B_74))])
    self.assertEqual(comparison.charts, [])
    self.assertIn("conflicting values for the same combination (76.3%, 77.0%)", comparison.rows[0].reason)

  def test_different_known_labels_are_not_a_conflict(self):
    ensemble = accuracy("78.1%", A_ENSEMBLE, variant={"label": "Ensemble of 10", "as_written": "ensemble of 10 models"})
    comparison = compare([source("S1", PAPER_A, accuracy("76.3%", A_76), ensemble), source("S2", PAPER_B, accuracy("74.9%", B_74))])
    (chart,) = comparison.charts
    # The ensemble is a separate labelled point, not a conflicting value of the single model.
    self.assertEqual([(point.variant, point.number) for point in chart.points], [
      ("Single model", 76.3), ("Ensemble of 10", 78.1), ("Single model", 74.9),
    ])
    self.assertNotIn("conflicting", " ".join(row.reason or "" for row in comparison.rows))

  # Supplied hardware is checked for every metric kind.

  def test_supplied_hardware_is_checked_for_quality_measurements_too(self):
    wrong = accuracy("76.3%", A_76, hardware={"label": "TPU v4", "as_written": "TPU v4 pod"})
    comparison = compare([source("S1", PAPER_A, wrong), source("S2", PAPER_B, accuracy("74.9%", B_74))])
    self.assertEqual(comparison.charts, [])
    self.assertIn('the hardware words "TPU v4 pod" are not in its quotation', comparison.rows[0].reason)
    quoted = accuracy("76.3%", A_76, hardware={
      "label": "8x V100", "as_written": "8 NVIDIA V100 GPUs", "page": 3, "quote": "on 8 NVIDIA V100 GPUs",
    })
    self.assertEqual(len(compare([source("S1", PAPER_A, quoted), source("S2", PAPER_B, accuracy("74.9%", B_74))]).charts), 1)

  def test_unknown_hardware_does_not_block_a_quality_measurement(self):
    unknown = accuracy("76.3%", A_76, hardware={"unknown": "Not stated."})
    self.assertEqual(len(compare([source("S1", PAPER_A, unknown), source("S2", PAPER_B, accuracy("74.9%", B_74))]).charts), 1)

  # Percent signs must agree between the unit and the quotation.

  def test_percent_signs_must_match_the_quotation(self):
    self.assertTrue(value_in_quote("91.2%", "reaches 91.2 % top-1"))
    self.assertTrue(value_in_quote("91.2%", "reaches 91.2 percent top-1"))
    self.assertFalse(value_in_quote("91.2%", "reaches 91.2 top-1"))
    self.assertFalse(value_in_quote("91.2", "reaches 91.2% top-1"))
    self.assertTrue(value_in_quote("91.2", "reaches 91.2 points"))
    as_points = {**accuracy("76.3", A_76), "unit": "points"}
    (row,) = compare([source("S1", PAPER_A, as_points)]).rows
    self.assertIn('the quotation shows "76.3" as a percentage, which does not match the unit "points"', row.reason)
    as_percent = accuracy("61.2%", B_MAP, metric={"label": "mAP@0.5", "as_written": "mAP@0.5"})
    (row,) = compare([source("S1", PAPER_B, as_percent)]).rows
    self.assertIn('the quotation shows "61.2" without a percent sign, which does not match the unit "%"', row.reason)

  def test_the_value_quotation_index_is_the_one_that_shows_the_number(self):
    measurement = accuracy("76.3%", A_76)
    measurement["evidence"] = [{"page": 1, "quote": DEFINITION_QUOTE}, {"page": 2, "quote": A_76}]
    comparison = compare([source("S1", PAPER_A, measurement), source("S2", PAPER_B, accuracy("74.9%", B_74))])
    (chart,) = comparison.charts
    self.assertEqual(chart.points[0].assessed.value_quote, 1)


# A recorded contradiction: the abstract (p.1) and Section 6 (p.3) of S1
# give different BLEU values for the same French test set.
CONTRA_ABSTRACT = "Abstract: the single model reaches 41.8 BLEU on the WMT French test set for translation under tokenized BLEU."
CONTRA_RESULTS = "Results: the single model reaches 41.8 BLEU on the WMT French test set for translation under tokenized BLEU."
CONTRA_GERMAN = "The single model reaches 28.4 BLEU on the WMT German test set for translation under tokenized BLEU."
CONTRA_SECTION = "In Section 6 the single model achieves 41.0 BLEU on the WMT French test set for translation under tokenized BLEU."
CONTRA_S1 = (CONTRA_ABSTRACT + "\n", CONTRA_RESULTS + "\n" + CONTRA_GERMAN + "\n", CONTRA_SECTION + "\n")
CONTRA_S2 = (
  "Our single model reaches 40.5 BLEU on the WMT French test set for translation under tokenized BLEU.\n"
  "Our single model reaches 27.1 BLEU on the WMT German test set for translation under tokenized BLEU.\n",
)


def translation(value_text, page, quote, language, *, dimension="results"):
  return {
    "dimension": dimension, "metric_kind": "quality", "value_text": value_text, "unit": "BLEU",
    "evidence": [{"page": page, "quote": quote}],
    "fields": {
      "task": {"label": "Machine translation", "as_written": "translation"},
      "dataset": {"label": f"WMT {language}", "as_written": f"WMT {language} test set"},
      "split": {"label": "test", "as_written": "test set"},
      "metric": {"label": "BLEU", "as_written": "BLEU"},
      "metric_definition": {"label": "Tokenized BLEU", "as_written": "tokenized BLEU"},
      "variant": {"label": "Single model", "as_written": "single model"},
    },
  }


def with_contradiction(*measurements, sides=None, pages=CONTRA_S1):
  findings = {"results": {
    "status": "reported",
    "value": "French BLEU is given as both 41.8 (abstract) and 41.0 (Section 6).",
    "evidence": [{"page": 2, "quote": CONTRA_RESULTS}],
    "contradictions": [{
      "description": "The abstract and Section 6 give different French BLEU.",
      "evidence": sides or [{"page": 1, "quote": CONTRA_ABSTRACT}, {"page": 3, "quote": CONTRA_SECTION}],
    }],
  }}
  return report_for(findings, source_id="S1", pages=pages, measurements=list(measurements))


S2_FRENCH = translation("40.5", 1, "Our single model reaches 40.5 BLEU on the WMT French test set for translation under tokenized BLEU.", "French")
S2_GERMAN = translation("27.1", 1, "Our single model reaches 27.1 BLEU on the WMT German test set for translation under tokenized BLEU.", "German")


class RecordedContradictionTest(unittest.TestCase):

  def s2(self):
    return source("S2", CONTRA_S2, S2_FRENCH, S2_GERMAN)

  def test_only_one_side_entered_as_a_measurement_is_never_plotted(self):
    # 41.8 is quoted from p.2, not from a contradiction side, but its number is a side's number.
    s1 = with_contradiction(translation("41.8", 2, CONTRA_RESULTS, "French"))
    comparison = compare([s1, self.s2()])
    self.assertEqual(comparison.charts, [])
    self.assertIn(
      "this value is part of a contradiction recorded in the source "
      "(\u201cThe abstract and Section 6 give different French BLEU\u201d)",
      comparison.rows[0].reason,
    )
    self.assertIn("(S1) is excluded", comparison.rows[1].reason)

  def test_the_same_number_written_differently_is_still_matched(self):
    # The abstract writes 41.80; the measurement records 41.8 from p.2.
    abstract = CONTRA_ABSTRACT.replace("41.8 BLEU", "41.80 BLEU")
    pages = (abstract + "\n", *CONTRA_S1[1:])
    sides = [{"page": 1, "quote": abstract}, {"page": 3, "quote": CONTRA_SECTION}]
    s1 = with_contradiction(translation("41.8", 2, CONTRA_RESULTS, "French"), sides=sides, pages=pages)
    comparison = compare([s1, self.s2()])
    self.assertEqual(comparison.charts, [])
    self.assertIn("part of a contradiction recorded in the source", comparison.rows[0].reason)

  def test_a_measurement_quoting_a_contradiction_side_is_never_plotted(self):
    s1 = with_contradiction(translation("41.0", 3, CONTRA_SECTION, "French"))
    comparison = compare([s1, self.s2()])
    self.assertEqual(comparison.charts, [])
    self.assertIn("part of a contradiction recorded in the source", comparison.rows[0].reason)

  def test_an_unrelated_value_in_the_same_dimension_can_still_be_plotted(self):
    s1 = with_contradiction(translation("28.4", 2, CONTRA_GERMAN, "German"))
    (chart,) = compare([s1, self.s2()]).charts
    self.assertEqual([point.number for point in chart.points], [28.4, 27.1])

  def test_an_unverifiable_contradiction_excludes_the_whole_dimension(self):
    sides = [{"page": 1, "quote": CONTRA_ABSTRACT}, {"page": 3, "quote": "In Section 6 the model achieves 39.9 BLEU overall."}]
    s1 = with_contradiction(translation("28.4", 2, CONTRA_GERMAN, "German"), sides=sides)
    comparison = compare([s1, self.s2()])
    self.assertEqual(comparison.charts, [])
    self.assertIn("could not all be verified, so no value from this dimension is plotted", comparison.rows[0].reason)

  def test_a_contradiction_only_affects_its_own_dimension(self):
    s1 = with_contradiction(translation("41.8", 2, CONTRA_RESULTS, "French", dimension="validation"))
    s2 = source("S2", CONTRA_S2, {**S2_FRENCH, "dimension": "validation"})
    (chart,) = compare([s1, s2]).charts
    self.assertEqual([point.number for point in chart.points], [41.8, 40.5])


if __name__ == "__main__":
  unittest.main()
