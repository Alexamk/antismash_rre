#  License: GNU Affero General Public License v3 or later
#  A copy of GNU AGPL v3 should have been included in this software package in LICENSE.txt.

'''
In-depth analysis and annotation of NRPS/PKS gene clusters.
'''

import logging
from typing import List

from antismash.common.secmet import Record, CDSFeature, AntismashDomain
from antismash.config import ConfigType

from .orderfinder import analyse_biosynthetic_order
from .parsers import calculate_consensus_prediction, modify_monomer_predictions
from .results import NRPS_PKS_Results
from .structure_drawer import generate_chemical_structure_preds
from .substrates import run_pks_substr_spec_predictions

from .nrps_predictor import run_nrpspredictor


def generate_structure_images(record: Record, results: NRPS_PKS_Results, options: ConfigType) -> None:
    """ Generate the structure images based on monomers prediction for all
        cluster features
    """
    compound_predictions = {key: val[0] for key, val in results.cluster_predictions.items()}
    if compound_predictions:
        generate_chemical_structure_preds(compound_predictions, record, options)


def get_a_domains_from_cds_features(record: Record, cds_features: List[CDSFeature]) -> List[AntismashDomain]:
    """ Fetches all AMP-binding AntismashDomains which are contained within the given
        CDS features.

        Arguments:
            record: the Record containing both AntismashDomains and CDSFeatures
            cds_features: the specific CDSFeatures from which to get the A-domains

        Returns:
            a list of AntismashDomains, one for each A domain found
    """
    a_domains = []
    for cds in cds_features:
        for domain in cds.nrps_pks.domains:
            if domain.name == "AMP-binding":
                a_domains.append(record.get_domain_by_name(domain.feature_name))
    return a_domains


def specific_analysis(record: Record, results: NRPS_PKS_Results, options: ConfigType) -> NRPS_PKS_Results:
    """ Runs the various NRPS/PKS analyses on a record and returns their results """
    nrps_pks_genes = record.get_nrps_pks_cds_features()

    if not nrps_pks_genes:
        logging.debug("No NRPS or PKS genes found, skipping analysis")
        return results

    a_domains = get_a_domains_from_cds_features(record, nrps_pks_genes)
    if a_domains:
        nrpspred_results = run_nrpspredictor(a_domains, options)
        for domain, res in zip(a_domains, nrpspred_results):
            results.nrps[domain.get_name()]["NRPSPredictor2"] = res

    results.pks = run_pks_substr_spec_predictions(nrps_pks_genes)
    results.consensus, results.consensus_transat = calculate_consensus_prediction(nrps_pks_genes,
                                                         results.pks.method_results, results.nrps)

    modify_monomer_predictions(nrps_pks_genes, results.consensus)

    results.cluster_predictions = analyse_biosynthetic_order(nrps_pks_genes, results.consensus, record)
    generate_structure_images(record, results, options)
    return results
