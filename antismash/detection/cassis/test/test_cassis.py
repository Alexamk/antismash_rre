# License: GNU Affero General Public License v3 or later
# A copy of GNU AGPL v3 should have been included in this software package in LICENSE.txt.

"""Test suite for the cassis cluster detection plugin"""

# for test files, silence irrelevant and noisy pylint warnings
# pylint: disable=no-self-use,protected-access,missing-docstring

import os
from tempfile import TemporaryDirectory
import unittest

from Bio.Seq import Seq
from Bio.SeqFeature import FeatureLocation
from minimock import mock, restore

from antismash.common import path, secmet
from antismash.common.test import helpers
from antismash.config import build_config, destroy_config
from antismash.detection import cassis
from antismash.detection.cassis.cluster_prediction import ClusterMarker
from antismash.detection.cassis.motifs import Motif
from antismash.detection.cassis.promoters import Promoter, CombinedPromoter, get_promoters

cassis.VERBOSE_DEBUG = True


def convert_newline(string):
    """Convert all line endings to \n for OS independency"""
    return "\n".join(string.splitlines())


def create_fake_record():
    """Set up a fake sequence record"""
    seq_record = helpers.DummyRecord(seq=Seq("acgtacgtacgtacgtacgtacgtacgtacgtacgtacgtacgtacgta" * 196))
    seq_record.name = "test"
    locations = [FeatureLocation(100, 300, strand=1), FeatureLocation(101, 299, strand=-1),
                 FeatureLocation(250, 350, strand=1), FeatureLocation(500, 1000, strand=1),
                 FeatureLocation(1111, 1500, strand=-1), FeatureLocation(2000, 2200, strand=-1),
                 FeatureLocation(2999, 4000, strand=1), FeatureLocation(4321, 5678, strand=1),
                 FeatureLocation(6660, 9000, strand=-1)]
    for i in range(9):
        cds = helpers.DummyCDS(locus_tag="gene" + str(i+1))
        cds.location = locations[i]
        seq_record.add_cds_feature(cds)
        seq_record.add_gene(secmet.Gene(locations[i], locus_tag="gene" + str(i+1)))
        if i == 3 or i == 5:
            cds.sec_met = secmet.qualifiers.SecMetQualifier({"faked"}, [])
            cds.gene_functions.add(secmet.feature.GeneFunction.CORE, "testtool", "dummy")

    return seq_record


class TestCassisMethods(unittest.TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory(prefix="as_cassis")
        self.options = build_config(["--cpus", "2", "--output-dir", self.tempdir.name],
                                    isolated=True, modules=[cassis])

    def tearDown(self):
        destroy_config()
        self.tempdir.cleanup()
        for subdir in ["meme", "fimo"]:
            assert not os.path.exists(path.get_full_path(__file__, subdir))
        assert not os.path.isfile(path.get_full_path(__file__, "data", "test_promoter_positions.csv"))
        assert not os.path.isfile(path.get_full_path(__file__, "data", "test_promoter_sequences.fasta"))

    def test_get_anchor_gene_names(self):
        anchor_genes = ["gene4", "gene6"]
        seq_record = create_fake_record()
        self.assertEqual(cassis.get_anchor_gene_names(seq_record), anchor_genes)

    def test_ignore_overlapping(self):
        expected_not_ignored = ["gene1", "gene4", "gene5", "gene6", "gene7", "gene8", "gene9"]
        expected_ignored = ["gene2", "gene3"]
        seq_record = create_fake_record()
        not_ignored, ignored = cassis.ignore_overlapping(seq_record.get_genes())

        self.assertEqual([x.locus_tag for x in ignored], expected_ignored)
        self.assertEqual([x.locus_tag for x in not_ignored], expected_not_ignored)

    def test_get_promoters(self):
        upstream_tss = 1000
        downstream_tss = 50
        seq_record = create_fake_record()
        genes, ignored_genes = cassis.ignore_overlapping(seq_record.get_genes())  # ignore ignored_genes

        # see cassis/promoterregions.png for details
        # [[start_prom1, end_prom1], [start_prom2, end_prom2], ...]
        expected_promoters = [
            [0, 150],
            [301, 550],
            [1450, 1999],
            [2150, 3049],
            [4001, 4371],
            [8950, 9603],
        ]

        promoters = get_promoters(seq_record, genes, upstream_tss, downstream_tss)
        self.assertEqual(list(map(lambda x: [x.start, x.end], promoters)), expected_promoters)
        cassis.write_promoters_to_file(self.options.output_dir, seq_record.name, promoters)
        # read expected files and save to string variable
        expected_sequences_file = ""
        with open(path.get_full_path(__file__, "data", "expected_promoter_sequences.fasta")) as handle:
            expected_sequences_file = handle.read()
        expected_sequences_file = convert_newline(expected_sequences_file.rstrip())

        expected_positions_file = ""
        with open(path.get_full_path(__file__, "data", "expected_promoter_positions.csv")) as handle:
            expected_positions_file = handle.read()
        expected_positions_file = convert_newline(expected_positions_file.rstrip())

        # read test files and save to string variable
        sequences_file = ""
        with open(os.path.join(self.options.output_dir, seq_record.name + "_promoter_sequences.fasta")) as handle:
            sequences_file = handle.read()
        sequences_file = convert_newline(sequences_file.rstrip())

        positions_file = ""
        with open(os.path.join(self.options.output_dir, seq_record.name + "_promoter_positions.csv")) as handle:
            positions_file = handle.read()
        positions_file = convert_newline(positions_file.rstrip())

        self.assertEqual(sequences_file, expected_sequences_file)
        self.assertEqual(positions_file, expected_positions_file)

    def test_cleanup_outdir(self):
        anchor_genes = ["gene1", "gene4"]
        cluster = cassis.ClusterPrediction(ClusterMarker("gene1", Motif(3, 3, score=1)),
                                           ClusterMarker("gene4", Motif(3, 3, score=1)))
        cluster.start.promoter = "gene1"
        cluster.end.promoter = "gene3+gene4"
        cluster.genes = 4
        cluster.promoters = 3
        cluster_predictions = {"gene1": [cluster]}

        # create some empty test dirs, which should be deleted during the test
        # prediction! --> keep!
        os.makedirs(os.path.join(self.options.output_dir, "meme", "gene1", "+03_-03"))
        # prediction! --> keep!
        os.makedirs(os.path.join(self.options.output_dir, "fimo", "gene1", "+03_-03"))
        # no prediction --> delete
        os.makedirs(os.path.join(self.options.output_dir, "meme", "gene1", "+04_-04"))
        # no prediction --> delete
        os.makedirs(os.path.join(self.options.output_dir, "fimo", "gene1", "+04_-04"))
        # no prediction --> delete
        os.makedirs(os.path.join(self.options.output_dir, "meme", "gene4", "+03_-03"))
        # no prediction --> delete
        os.makedirs(os.path.join(self.options.output_dir, "fimo", "gene4", "+03_-03"))
        # prediction for this gene, but not from this motif --> delete
        os.makedirs(os.path.join(self.options.output_dir, "meme", "gene4", "+04_-04"))
        # prediction for this gene, but not from this motif --> delete
        os.makedirs(os.path.join(self.options.output_dir, "fimo", "gene4", "+04_-04"))

        cassis.cleanup_outdir(anchor_genes, cluster_predictions, self.options)

        # assert kept directories
        self.assertTrue("gene1" in os.listdir(os.path.join(self.options.output_dir, "meme")))
        self.assertTrue("gene1" in os.listdir(os.path.join(self.options.output_dir, "fimo")))
        self.assertTrue("+03_-03" in os.listdir(os.path.join(self.options.output_dir, "meme", "gene1")))
        self.assertTrue("+03_-03" in os.listdir(os.path.join(self.options.output_dir, "fimo", "gene1")))

        # assert deleted directories
        self.assertTrue("gene4" not in os.listdir(os.path.join(self.options.output_dir, "meme")))
        self.assertTrue("gene4" not in os.listdir(os.path.join(self.options.output_dir, "fimo")))
        self.assertTrue("+04_-04" not in os.listdir(os.path.join(self.options.output_dir, "meme", "gene1")))
        self.assertTrue("+04_-04" not in os.listdir(os.path.join(self.options.output_dir, "fimo", "gene1")))


class TestMotifRepresentation(unittest.TestCase):
    def test_conversion(self):
        motif = Motif(3, 3)
        assert motif.pairing_string == "+03_-03"
        motif.plus = 4
        assert motif.pairing_string == "+04_-03"
        motif.minus = 2
        assert motif.pairing_string == "+04_-02"


class TestPromoters(unittest.TestCase):
    def test_promoter_id(self):
        assert Promoter("gene1", 1, 5).get_id() == "gene1"
        assert CombinedPromoter("gene1", "gene2", 1, 5).get_id() == "gene1+gene2"


class TestCassisStorageMethods(unittest.TestCase):
    def test_store_promoters(self):
        promoters = [Promoter("gene1", 10, 20, seq=Seq("cgtacgtacgt")),
                     Promoter("gene2", 30, 40, seq=Seq("cgtacgtacgt")),
                     CombinedPromoter("gene3", "gene4", 50, 60, seq=Seq("cgtacgtacgt"))]
        record_with_promoters = create_fake_record()
        cassis.store_promoters(promoters, record_with_promoters)  # add ("store") promoters to seq_record

        record_without_promoters = create_fake_record()  # just the same, without adding promoters

        # test if store_promoters changed any non-promoter feature (should not!)  # TODO

        # test promoter features
        expected_count = record_without_promoters.get_feature_count() + len(promoters)
        assert expected_count == record_with_promoters.get_feature_count()
        for i in range(len(promoters)):
            feature = record_with_promoters.get_generics()[i]
            assert feature.type == "promoter"
            assert feature.get_qualifier("seq") == ("cgtacgtacgt",)

        # especially test bidirectional promoter feature (third promoter, last feature)
        last_promoter = record_with_promoters.get_generics()[-1]
        assert last_promoter.get_qualifier("locus_tag") == ("gene3", "gene4")
        assert last_promoter.notes == ["bidirectional promoter"]

    def test_store_clusters(self):
        # this test is similar to test_store_promoters
        anchor = "gene3"

        start_marker = ClusterMarker("gene1", Motif(3, 3, score=1))
        start_marker.promoter = "gene1"
        start_marker.abundance = 2
        end_marker = ClusterMarker("gene4", Motif(3, 3, score=1))
        end_marker.promoter = "gene3+gene4"
        assert end_marker.abundance == 1
        first_cluster = cassis.ClusterPrediction(start_marker, end_marker)
        first_cluster.promoters = 3
        first_cluster.genes = 4

        start_marker = ClusterMarker("gene1", Motif(4, 4, score=1))
        start_marker.promoter = "gene1"
        assert start_marker.abundance == 1
        end_marker = ClusterMarker("gene5", Motif(4, 4, score=1))
        end_marker.promoter = "gene5"
        assert end_marker.abundance == 1
        second_cluster = cassis.ClusterPrediction(start_marker, end_marker)
        second_cluster.promoters = 3
        second_cluster.genes = 4

        clusters = [first_cluster, second_cluster]

        record_with_clusters = create_fake_record()
        record_without_clusters = create_fake_record()  # just the same, without adding clusters

        borders = cassis.create_cluster_borders(anchor, clusters, record_with_clusters)
        assert record_with_clusters.get_feature_count() == record_without_clusters.get_feature_count()

        for border in borders:
            record_with_clusters.add_cluster_border(border)

        # test if store_clusters changed any non-cluster feature (should not!)  # TODO

        # test cluster features
        assert record_without_clusters.get_feature_count() + len(clusters) == record_with_clusters.get_feature_count()
        for i, cluster in enumerate(clusters):
            cluster_border = record_with_clusters.get_cluster_borders()[i]
            self.assertEqual(cluster_border.type, "cluster_border")
            self.assertEqual(cluster_border.tool, "cassis")
            self.assertEqual(cluster_border.get_qualifier("anchor"), (anchor,))
            self.assertEqual(cluster_border.get_qualifier("genes"), (cluster.genes,))
            self.assertEqual(cluster_border.get_qualifier("promoters"), (cluster.promoters,))
            self.assertEqual(cluster_border.get_qualifier("gene_left"), (cluster.start.gene,))
            self.assertEqual(cluster_border.get_qualifier("gene_right"), (cluster.end.gene,))
            # don't test all feature qualifiers, only some


class TestResults(unittest.TestCase):
    def setUp(self):
        self.old_max_perc = cassis.MAX_PERCENTAGE
        self.old_max_gap = cassis.MAX_GAP_LENGTH

    def tearDown(self):
        cassis.MAX_PERCENTAGE = self.old_max_perc
        cassis.MAX_GAP_LENGTH = self.old_max_gap
        restore()

    def test_base(self):
        results = cassis.CassisResults("test")
        assert results.record_id == "test"
        assert results.borders == []
        assert results.promoters == []

    def test_regeneration(self):
        record = create_fake_record()
        results = cassis.CassisResults(record.id)
        # create a prediction, since it will generate a border with many extra qualifiers
        start_marker = ClusterMarker("gene1", Motif(3, 3, score=1))
        start_marker.promoter = "gene1"
        start_marker.abundance = 2
        end_marker = ClusterMarker("gene4", Motif(3, 3, score=1))
        end_marker.promoter = "gene3+gene4"
        assert end_marker.abundance == 1
        cluster = cassis.ClusterPrediction(start_marker, end_marker)
        results.borders = cassis.create_cluster_borders("gene1", [cluster], record)
        assert results.borders

        results.promoters = [Promoter("gene1", 10, 20, seq=Seq("cgtacgtacgt")),
                             Promoter("gene2", 30, 40, seq=Seq("cgtacgtacgt")),
                             CombinedPromoter("gene3", "gene4", 50, 60, seq=Seq("cgtacgtacgt"))]

        round_trip = cassis.regenerate_previous_results(results.to_json(), record, None)
        assert isinstance(round_trip, cassis.CassisResults)
        assert len(results.borders) == len(round_trip.borders)
        for old, new in zip(results.borders, round_trip.borders):
            assert old.location == new.location
            assert old.to_biopython()[0].qualifiers == new.to_biopython()[0].qualifiers
        assert round_trip.promoters == results.promoters

    def test_changed_max_percentage(self):
        record = create_fake_record()
        json = cassis.CassisResults(record.id).to_json()
        assert isinstance(cassis.regenerate_previous_results(json, record, None),
                          cassis.CassisResults)
        cassis.MAX_PERCENTAGE += 5
        assert cassis.regenerate_previous_results(json, record, None) is None

    def test_changed_max_gap_length(self):
        record = create_fake_record()
        json = cassis.CassisResults(record.id).to_json()
        assert isinstance(cassis.regenerate_previous_results(json, record, None),
                          cassis.CassisResults)
        cassis.MAX_GAP_LENGTH += 1
        assert cassis.regenerate_previous_results(json, record, None) is None

    def test_not_same_record(self):
        record = create_fake_record()
        record.id = "A"
        other = create_fake_record()
        other.id = "B"
        json = cassis.CassisResults(record.id).to_json()
        assert isinstance(cassis.regenerate_previous_results(json, record, None),
                          cassis.CassisResults)
        assert cassis.regenerate_previous_results(json, other, None) is None

    def test_run_on_record_skips_work(self):
        record = create_fake_record()
        record.id = "real"
        results = cassis.CassisResults(record.id)

        mock("cassis.detect", returns=cassis.CassisResults("fake"))
        assert cassis.run_on_record(record, results, None).record_id == "real"
        assert cassis.run_on_record(record, None, None).record_id == "fake"