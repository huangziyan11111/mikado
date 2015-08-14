# coding: utf-8

"""
This class is the main workhorse of the compare.py utility.
"""

import sys
import csv
from logging import handlers as log_handlers
import queue
import logging
import collections
import argparse
import bisect
import operator
import itertools
from collections import namedtuple
from mikado_lib.scales.restultstorer import RestultStorer
from mikado_lib.loci_objects.transcript import Transcript
import mikado_lib.exceptions
from mikado_lib.scales.accountant import Accountant


class Assigner:

    """
    This class has the purpose of assiging each prediction transcript to its best match
    among the reference transcripts.
    """

    def __init__(self, genes: dict, positions: collections.defaultdict, args: argparse.Namespace,
                 stat_calculator: Accountant):

        """

        :param genes: a dictionary which contains the gene containers for the reference transcript objects.
        :type genes: dict

        :param positions: a defaultdict which is used for fast lookup of genomic positions
        :type positions: collections.defaultdict

        :param args: the parameters passed through the command line
        :type args: None | argparse.Namespace

        :param stat_calculator: an instance of Accountant, to which the results will be sent to.
        :type stat_calculator: Accountant
        """

        if args is None:
            self.args = argparse.Namespace
            self.args.out = sys.stdout
            self.args.distance = 2000
            self.args.protein_coding = False
            self.args.loq_queue = queue.Queue()
            self.args.exclude_utr = False
            self.args.verbose = False
        else:
            self.args = args

        self.queue_handler = log_handlers.QueueHandler(self.args.log_queue)
        self.logger = logging.getLogger("Assigner")
        self.logger.addHandler(self.queue_handler)
        if args.verbose:
            self.logger.setLevel(logging.DEBUG)
        else:
            self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

        self.genes = genes
        self.positions = positions
        self.gene_matches = dict()
        for gid in genes:
            self.gene_matches[gid] = dict()
            for tid in genes[gid]:
                self.gene_matches[gid][tid.id] = []

        self.indexer = collections.defaultdict(list).fromkeys(self.positions)

        for chrom in positions:
            self.indexer[chrom] = sorted(self.positions[chrom].keys())

        self.tmap_out = open("{0}.tmap".format(args.out), 'wt')
        self.tmap_rower = csv.DictWriter(self.tmap_out, RestultStorer.__slots__, delimiter="\t")
        self.tmap_rower.writeheader()
        self.done = 0
        self.stat_calculator = stat_calculator

    def add_to_refmap(self, result: RestultStorer) -> None:
        """
        :param result: the result of the compare function

        This method verifies whether the comparison in input is the best available for the
        candidate reference gene, and loads it into the refmap dictionary if such is the case.

        """

        if result is not None and result.ccode != ("u",):
            # This is necessary for fusion genes
            if len(result.RefGene) == 1:
                self.gene_matches[result.RefGene[0]][result.RefId[0]].append(result)

            else:  # Fusion gene

                for index, (gid, tid) in enumerate(zip(result.RefGene, result.RefId)):
                    new_result = RestultStorer(gid, tid, ["f", result.ccode[index + 1]], result.TID, result.GID,
                                               result.n_prec[index], result.n_recall[index], result.n_f1[index],
                                               result.j_prec[index], result.j_recall[index], result.j_f1[index],
                                               0
                                               )
                    self.gene_matches[gid][tid].append(new_result)
        return

    @classmethod
    def find_neighbours(cls, keys, position, distance=2000):
        """
        This class method is used to find the possible matches of a given prediction key.
        :param keys: the start
        :type keys: [(int,int)]

        :param position: the position of my prediction in the genome
        :type position: (int, int)

        :param distance: optional maximum distance of a prediction from reference before being called an unknown
        :type distance: int

        :return:
        """

        start, end = position

        # This should happen only if we are analysing a prediction from a scaffold
        # with no reference annotation on it
        if len(keys) == 0:
            return []

        indexed = bisect.bisect(keys, position)

        found = []

        search_right = True
        search_left = True

        left_index = max(0, min(indexed, len(keys) - 1))  # Must be a valid list index
        right_index = min(len(keys) - 1, left_index + 1)
        if right_index == left_index:
            search_right = False

        while search_left is True:
            if keys[left_index][1] + distance < start:
                search_left = False
                continue
            found.append(keys[left_index])
            left_index -= 1
            if left_index < 0:
                search_left = False

        while search_right is True:
            if keys[right_index][0] - distance > end:
                search_right = False
                continue
            found.append(keys[right_index])
            right_index += 1
            if right_index >= len(keys):
                search_right = False

        distances = []
        for key in found:
            # Append the key (to be used later for retrieval) and the distance
            distances.append(
                (key,
                 max(0,
                     max(start, key[0]) - min(end, key[1])
                     )
                 )
            )

        # Sort by distance
        distances = sorted(distances, key=operator.itemgetter(1))

        return distances

    @staticmethod
    def dubious_getter(dubious_result):
        """
        Function used to perform the sorting of the matches.
        :param dubious_result: a result
        :type dubious_result: ResultStorer
        """
        getter = operator.attrgetter("j_f1", "n_f1")
        return getter(dubious_result[0])

    @staticmethod
    def get_f1(curr_result):
        """
        Simple getter for F1 statistics (N_F1 and J_F1)
        :param curr_result: a result storer
        :type curr_result: ResultStorer

        :return: (J_F1, N_F1)
        :rtype (float, float)
        """
        return curr_result.j_f1[0], curr_result.n_f1[0]

    def get_best(self, prediction: Transcript):

        """
        :param prediction: the candidate transcript to be analysed
        :type prediction: Transcript

        This function will get the best possible assignment for each transcript.
        Fusion genes are called when the following conditions are verified:
        - the prediction intersects (at least) two transcripts in (at least) two different loci
        - the suspected fusion transcript lies on the same strand of all candidate fused genes
        - each candidate transcript has at least one fusion or 10% of its nucleotides covered by the fusion transcript.
        The 10% threshold is hard-coded in the function. 
        """

        self.logger.debug("Started with {0}".format(prediction.id))

        # Prepare the prediction to be analysed
        prediction.logger = self.logger
        try:
            prediction.finalize()
            if self.args.exclude_utr is True:
                prediction.remove_utrs()
        except mikado_lib.exceptions.InvalidCDS:
            try:
                prediction.strip_cds()
            except mikado_lib.exceptions.InvalidTranscript as err:
                self.logger.warn("Invalid transcript: {0}".format(prediction.id))
                self.logger.warn("Error message: {0}".format(err))
                self.done += 1
                self.print_tmap(None)
                return None
        except mikado_lib.exceptions.InvalidTranscript as err:
            #         args.queue.put_nowait("mock")
            self.logger.warn("Invalid transcript: {0}".format(prediction.id))
            self.logger.warn("Error message: {0}".format(err))
            self.done += 1
            self.print_tmap(None)
            return None

        # Ignore non-coding RNAs if we are interested in protein-coding transcripts only
        if self.args.protein_coding is True and prediction.combined_cds_length == 0:
            #         args.queue.put_nowait("mock")
            self.logger.debug("No CDS for {0}. Ignoring.".format(prediction.id))
            self.done += 1
            self.print_tmap(None)
            return None

        if prediction.chrom in self.indexer:
            keys = self.indexer[prediction.chrom]
        else:
            keys = []

        distances = self.find_neighbours(keys,
                                         (prediction.start, prediction.end),
                                         distance=self.args.distance
                                         )
        self.logger.debug("Distances for {0}: {1}".format(prediction.id, distances))

        # Unknown transcript
        if len(distances) == 0 or distances[0][1] > self.args.distance:
            ccode = "u"
            best_result = RestultStorer("-", "-", ccode, prediction.id, ",".join(prediction.parent), *[0] * 6 + ["-"])
            self.stat_calculator.store(prediction, best_result, None)
            results = [best_result]

            # Polymerase run-on
        else:
            if distances[0][1] > 0:
                match = self.positions[prediction.chrom][distances[0][0]][0]
                results = []
                for reference in match:
                    results.append(self.calc_compare(prediction, reference))

                best_result = sorted(results,
                                     key=operator.attrgetter("distance"))[0]
            else:
                matches = list(filter(lambda x: x[1] == 0, distances))
                self.logger.debug("Matches for {0}: {1}".format(prediction.id,
                                                                matches))

                if len(matches) > 1:
                    self.logger.debug("More than on match for {0}: {1}".format(prediction.id,
                                                                               matches))

                    # Possible fusion
                    # Get the best match for each index
                    new_matches = []

                    for match in matches:
                        gene_matches = self.positions[prediction.chrom][match[0]]
                        temp_results = []
                        # This will result in calculating the matches twice unfortunately
                        for gm in gene_matches:
                            best_res = sorted([self.calc_compare(prediction, tra) for tra in gm],
                                              reverse=True,
                                              key=self.get_f1)[0]

                            temp_results.append((gm, best_res))
                        self.logger.debug("Temp results: {0}".format(temp_results))
                        best_res = sorted(temp_results, key=lambda x: self.get_f1(x[1]),
                                          reverse=True)[0]
                        self.logger.debug("Best result: {0}".format(best_res))
                        new_matches.append(best_res[0])

                    matches = sorted(new_matches)

                    strands = set(x.strand for x in matches)
                    if len(strands) > 1 and prediction.strand in strands:
                        matches = list(filter(lambda mmatch: mmatch.strand == prediction.strand, matches))
                    if len(matches) == 0:
                        raise ValueError("I filtered out all matches. This is wrong!")

                    best_fusion_results = []
                    results = []  # Final results
                    dubious = []  # Necessary for a double check.

                    for match in matches:
                        m_res = sorted([self.calc_compare(prediction, tra) for tra in match],
                                       reverse=True,
                                       key=self.get_f1)
                        # A fusion is called only if I have one of the following conditions:
                        # the transcript gets one of the junctions of the other transcript
                        # the exonic overlap is >=10% (n_recall)_

                        if m_res[0].j_f1[0] == 0 and m_res[0].n_recall[0] < 10:
                            dubious.append(m_res)
                            continue
                        results.extend(m_res)
                        best_fusion_results.append(m_res[0])

                    if len(results) == 0:
                        # I have filtered out all the results, because I only match partially the reference genes
                        dubious = sorted(dubious, key=self.dubious_getter)
                        results = dubious[0]
                        best_fusion_results = [results[0]]

                    values = []
                    for key in RestultStorer.__slots__:
                        if key in ["GID", "TID", "distance"]:
                            values.append(getattr(best_fusion_results[0], key))
                        elif key == "ccode":
                            if len(best_fusion_results) > 1:
                                values.append(tuple(["f"] + [getattr(x, key)[0] for x in
                                                             best_fusion_results]))  # Add the "f" ccode for fusions
                            else:
                                values.append(tuple(getattr(best_fusion_results[0], key)))
                        else:
                            val = tuple([getattr(x, key)[0] for x in best_fusion_results])
                            assert len(val) == len(best_fusion_results)
                            assert type(val[0]) is not tuple, val
                            values.append(val)

                    best_result = RestultStorer(*values)
                else:
                    matches = self.positions[prediction.chrom][matches[0][0]]
                    self.logger.debug("")
                    results = []
                    for match in matches:
                        self.logger.debug("{0}: type {1}".format(repr(match), type(match)))
                        results.extend([self.calc_compare(prediction, tra) for tra in match])

                    results = sorted(results, reverse=True,
                                     key=operator.attrgetter("j_f1", "n_f1"))

                    # if not (len(results) == len(match.transcripts) and len(results) > 0):
                    #     raise ValueError((match, str(prediction)))
                    best_result = results[0]
                    #     args.queue.put_nowait(result)
                    #     args.refmap_queue.put_nowait(result)
                    #     args.stats_queue.put_nowait((tr,result))
                    #     return result

        for result in results:
            self.add_to_refmap(result)
        self.logger.debug("Finished with {0}".format(prediction.id))
        self.print_tmap(best_result)
        self.done += 1
        return best_result

    def finish(self):
        """
        This function is called upon completion of the input file parsing. It will print out the
        reference file assignments into the refmap file, and the final statistics into the stats file.
        """
        self.logger.info("Finished parsing, total: {0} transcripts.".format(self.done))
        self.refmap_printer()
        self.stat_calculator.print_stats()

    def calc_compare(self, prediction: Transcript, reference: Transcript) -> RestultStorer:
        """Thin layer around the calc_compare class method.

        :param prediction: a Transcript instance.

        :param reference: a Transcript instance to which the prediction is compared to.

        :rtype ResultStorer

        """

        result, reference_exon = self.compare(prediction, reference)

        assert reference_exon is None or reference_exon in reference.exons
        self.stat_calculator.store(prediction, result, reference_exon)

        return result

    @classmethod
    def compare(cls, prediction: Transcript, reference: Transcript) -> (RestultStorer, tuple):

        """Function to compare two transcripts and determine a ccode.

        :param prediction: the transcript query
        :type prediction: Transcript

        :param reference: the reference transcript against which we desire to calculate the ccode and other stats.
        :type reference: Transcript

        :rtype (ResultStorer, (int,int)) | (ResultStorer, None)

        Available ccodes (from Cufflinks documentation):
        
        - =    Complete intron chain match
        - c    Contained
        - j    Potentially novel isoform (fragment): at least one splice junction is shared with a reference transcript
        - e    Single exon transfrag overlapping a reference exon and at least 10 bp of a reference intron,
               indicating a possible pre-mRNA fragment.
        - i    A *monoexonic* transfrag falling entirely within a reference intron
        - o    Generic exonic overlap with a reference transcript
        - p    Possible polymerase run-on fragment (within 2Kbases of a reference transcript)
        - u    Unknown, intergenic transcript
        - x    Exonic overlap with reference on the opposite strand
    
        Please note that the description for i is changed from Cufflinks.
    
        We also provide the following additional classifications:
        
        - f    gene fusion - in this case, this ccode will be followed by the ccodes of the matches for each gene,
               separated by comma
        - _    Complete match, for monoexonic transcripts (nucleotide F1>=95% - i.e. min(precision,recall)>=90.4%
        - m    Exon overlap between two monoexonic transcripts
        - n    Potentially novel isoform, where all the known junctions have been confirmed and we have
               added others as well *externally*
        - I    *multiexonic* transcript falling completely inside a known transcript
        - h    the transcript is multiexonic and extends a monoexonic reference transcript
        - O    Reverse generic overlap - the reference is monoexonic and overlaps the prediction
        - K    Reverse intron retention - the annotated gene model retains an intron compared to the prediction
        - P    Possible polymerase run-on fragment (within 2Kbases of a reference transcript), on the opposite strand
    
        This is a class method, and can therefore be used from outside the compare script.
        """

        prediction_nucls = set(itertools.chain(*[range(x[0], x[1] + 1) for x in prediction.exons]))
        reference_nucls = set(itertools.chain(*[range(x[0], x[1] + 1) for x in reference.exons]))

        nucl_overlap = len(set.intersection(reference_nucls, prediction_nucls))

        assert nucl_overlap <= min(reference.cdna_length, prediction.cdna_length), \
            (prediction.id, prediction.cdna_length, reference.id, reference.cdna_length, nucl_overlap)

        nucl_recall = nucl_overlap / reference.cdna_length  # Sensitivity
        nucl_precision = nucl_overlap / prediction.cdna_length
        if max(nucl_recall, nucl_precision) == 0:
            nucl_f1 = 0
        else:
            nucl_f1 = 2 * (nucl_recall * nucl_precision) / (nucl_recall + nucl_precision)

        reference_exon = None

        # Both multiexonic
        if min(prediction.exon_num, reference.exon_num) > 1:
            assert min(len(prediction.splices), len(reference.splices)) > 0, (prediction.introns, prediction.splices)
            one_intron_confirmed = any(intron in reference.introns for intron in prediction.introns)
            junction_overlap = len(set.intersection(set(prediction.splices), set(reference.splices)))
            junction_recall = junction_overlap / len(reference.splices)
            junction_precision = junction_overlap / len(prediction.splices)
            if max(junction_recall, junction_precision) > 0:
                junction_f1 = 2 * (junction_recall * junction_precision) / (junction_recall + junction_precision)
            else:
                junction_f1 = 0

        elif prediction.exon_num == reference.exon_num == 1:
            # junction_overlap = junction_f1 = junction_precision = junction_recall = 1
            junction_f1 = junction_precision = junction_recall = 1
        else:
            # junction_overlap = junction_f1 = junction_precision = junction_recall = 0
            junction_f1 = junction_precision = junction_recall = 0

        ccode = None
        distance = 0
        if junction_f1 == 1 and prediction.exon_num > 1:
            if prediction.strand == reference.strand or prediction.strand is None:
                ccode = "="  # We have recovered all the junctions
            else:
                ccode = "c"  # We will set this to x at the end of the function

        elif junction_f1 == 1 and nucl_f1 >= 0.95:
            reference_exon = reference.exons[0]
            if prediction.strand == reference.strand or prediction.strand is None:
                ccode = "_"  # We have recovered all the junctions
            else:
                ccode = "x"

        # Outside the transcript - polymerase run-on
        elif prediction.start > reference.end or prediction.end < reference.start:
            if reference.strand == prediction.strand:
                ccode = "p"
            else:
                ccode = "P"
            distance = max(prediction.start - reference.end, reference.start - prediction.end)

        elif nucl_precision == 1:
            if prediction.exon_num == 1 or (prediction.exon_num > 1 and junction_precision == 1):
                ccode = "c"

        if ccode is None:
            if min(prediction.exon_num, reference.exon_num) > 1:
                if junction_recall == 1 and junction_precision < 1:
                    new_splices = set.difference(set(prediction.splices), set(reference.splices))
                    if any(min(reference.splices) < splice < max(reference.splices) for splice in new_splices):
                        ccode = "j"
                    else:
                        ccode = "n"  # we have recovered all the junctions AND added some reference junctions of our own
                elif junction_recall > 0 and 0 < junction_precision < 1:
                    if one_intron_confirmed is True:
                        ccode = "j"
                    else:
                        ccode = "o"
                elif junction_precision == 1:
                    ccode = "c"
                    if nucl_precision < 1:
                        for intron in reference.introns:
                            if intron in prediction.introns:
                                continue
                            if intron[1] < prediction.start:
                                continue
                            elif intron[0] > prediction.end:
                                continue
                            if prediction.start < intron[0] and intron[1] < prediction.end:
                                ccode = "j"
                                break

                elif junction_recall == 0 and junction_precision == 0:
                    if nucl_f1 > 0:
                        ccode = "o"
                    else:
                        if nucl_overlap == 0:
                            # The only explanation for no nucleotide overlap
                            # and no junction overlap is that it is inside an intron
                            if reference.start < prediction.start < reference.end:
                                ccode = "I"
                            elif prediction.start < reference.start < prediction.end:
                                ccode = "K"  # reverse intron retention
            else:
                if prediction.exon_num == 1 and reference.exon_num > 1:
                    if nucl_precision < 1 and nucl_overlap > 0:
                        # Fraction outside
                        outside = max(reference.start - prediction.start, 0) + max(prediction.end - reference.end, 0)
                        if prediction.cdna_length - nucl_overlap - outside > 10:
                            ccode = "e"
                        else:
                            ccode = "o"
                    elif nucl_overlap > 0:
                        ccode = "o"
                    elif nucl_recall == 0 and reference.start < prediction.start < reference.end:
                        ccode = "i"  # Monoexonic fragment inside an intron
                elif prediction.exon_num > 1 and reference.exon_num == 1:
                    if nucl_recall == 1:
                        ccode = "n"  # Extension
                    else:
                        ccode = "O"  # Reverse generic overlap
                elif prediction.exon_num == reference.exon_num == 1:
                    junction_f1 = junction_precision = junction_recall = 1  # Set to one
                    if nucl_f1 >= 0.95 and reference.strand == prediction.strand:
                        reference_exon = reference.exons[0]
                        ccode = "_"
                    elif nucl_precision == 1:
                        ccode = "c"  # contained
                    else:
                        ccode = "m"  # just a generic exon overlap b/w two monoexonic transcripts

        if ccode in ("e", "o", "c", "m") and prediction.strand != reference.strand and all([x is not None for x in
                                                                                            (prediction.strand,
                                                                                             reference.strand)]):
            ccode = "x"

        if prediction.strand != reference.strand:
            reference_exon = None

        result = RestultStorer(reference.id, ",".join(reference.parent), ccode, prediction.id,
                               ",".join(prediction.parent),
                               round(nucl_precision * 100, 2), round(100 * nucl_recall, 2), round(100 * nucl_f1, 2),
                               round(junction_precision * 100, 2), round(100 * junction_recall, 2),
                               round(100 * junction_f1, 2),
                               distance
                               )
        if ccode is None:
            raise ValueError("Ccode is null;\n{0}".format(repr(result)))

        return result, reference_exon

    def print_tmap(self, res: RestultStorer):
        """
        This method will print a ResultStorer instance onto the TMAP file.
        :param res: result from compare
        :type res: ResultStorer
        """
        if self.done % 10000 == 0 and self.done > 0:
            self.logger.info("Done {0:,} transcripts".format(self.done))
        elif self.done % 1000 == 0 and self.done > 0:
            self.logger.debug("Done {0:,} transcripts".format(self.done))
        if res is not None:
            if type(res) is not RestultStorer:
                self.logger.exception("Wrong type for res: {0}".format(type(res)))
                self.logger.exception(repr(res))
                raise ValueError
            else:
                self.tmap_rower.writerow(res._asdict())

    def refmap_printer(self) -> None:

        """Function to print out the best match for each gene."""
        self.logger.info("Starting printing RefMap")
        with open("{0}.refmap".format(self.args.out), 'wt') as out:
            fields = ["RefId", "ccode", "TID", "GID", "RefGene", "best_ccode", "best_TID", "best_GID"]
            out_tuple = namedtuple("refmap", fields)

            rower = csv.DictWriter(out, fields, delimiter="\t")
            rower.writeheader()

            for gid in sorted(self.gene_matches.keys()):

                rows = []
                best_picks = []
                assert len(self.gene_matches[gid].keys()) > 0
                for tid in sorted(self.gene_matches[gid].keys()):
                    if len(self.gene_matches[gid][tid]) == 0:
                        row = tuple([tid, gid, "NA", "NA", "NA"])
                    else:
                        if any(True if (x.j_f1[0] > 0 or x.n_f1[0] > 0) else False
                               for x in self.gene_matches[gid][tid]):
                            best = sorted(self.gene_matches[gid][tid],
                                          key=operator.attrgetter("j_f1", "n_f1"), reverse=True)[0]
                        else:
                            best = sorted(self.gene_matches[gid][tid],
                                          key=operator.attrgetter("distance"), reverse=True)
                        best_picks.append(best)
                        if len(best.ccode) == 1:
                            row = tuple([tid, gid, ",".join(best.ccode), best.TID, best.GID])
                        else:
                            row = tuple([tid, gid, ",".join(best.ccode), best.TID, best.GID])

                    rows.append(row)

                if len(best_picks) > 0:
                    best_pick = sorted(best_picks, key=operator.attrgetter("j_f1", "n_f1"), reverse=True)[0]
                else:
                    best_pick = None

                for row in rows:
                    if best_pick is not None:
                        assert row[2] != "NA"
                        row = out_tuple(row[0], row[2], row[3], row[4], row[1], ",".join(best_pick.ccode),
                                        best_pick.TID, best_pick.GID)
                    else:
                        row = out_tuple(row[0], "NA", "NA", "NA", row[1], "NA", "NA", "NA")
                    rower.writerow(row._asdict())
        self.logger.info("Finished printing RefMap")
        return None
