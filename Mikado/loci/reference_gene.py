# coding: utf-8

"""
Pretty basic class that defines a reference gene with its transcripts.
Minimal checks.
"""

import logging
import operator
from .transcript import Transcript
from ..exceptions import InvalidTranscript, InvalidCDS
from ..parsers.GFF import GffLine
from ..parsers.GTF import GtfLine


class Gene:

    """
    :param transcr: a transcript used to initialize the container.
    :param gid:Id of the gene.
    :param logger: an optional Logger from the logging module.
    """

    def __init__(self, transcr: [None, Transcript], gid=None, logger=None):

        self.transcripts = dict()
        self.logger = None
        self.set_logger(logger)
        self.__introns = None
        self.exception_message = ''
        self.chrom, self.source, self.start, self.end, self.strand = [None] * 5

        if transcr is not None:
            if isinstance(transcr, Transcript):
                self.transcripts[transcr.id] = transcr
                self.id = transcr.parent[0]
            elif isinstance(transcr, GffLine):
                assert transcr.is_gene is True
                self.id = transcr.id
            elif isinstance(transcr, GtfLine):
                self.id = transcr.gene

            self.chrom, self.source, self.start, self.end, self.strand = (transcr.chrom,
                                                                          transcr.source,
                                                                          transcr.start,
                                                                          transcr.end,
                                                                          transcr.strand)
            self.transcripts[transcr.id] = transcr

        if gid is not None:
            self.id = gid

    def set_logger(self, logger):
        """
        :param logger: a Logger instance.
        :type logger: None | logging.Logger

        """
        if logger is None:
            return
        else:
            assert isinstance(logger, logging.Logger)
            self.logger = logger
        for tid in self.transcripts:
            self.transcripts[tid].logger = logger

    def add(self, transcr: Transcript):
        """
        This method adds a transcript to the storage.
        :param transcr: the transcript to be added.
        """

        self.start = min(self.start, transcr.start)
        self.end = max(self.end, transcr.end)
        self.transcripts[transcr.id] = transcr
        if transcr.strand != self.strand:
            if self.strand is None:
                self.strand = transcr.strand
            elif transcr.strand is None:
                transcr.strand = self.strand
            else:
                raise AssertionError("Discrepant strands for gene {0} and transcript {1}".format(
                    self.id, transcr.id
                ))

    def __getitem__(self, tid: str) -> Transcript:
        return self.transcripts[tid]

    def finalize(self, exclude_utr=False):
        """
        This method will finalize the container by checking the consistency of all the
        transcripts and eventually removing incorrect ones.

        :param exclude_utr: boolean flag
        :return:
        """

        to_remove = set()
        for tid in self.transcripts:
            try:
                self.transcripts[tid].finalize()
                if exclude_utr is True:
                    self.transcripts[tid].remove_utrs()
            except InvalidCDS:
                self.transcripts[tid].strip_cds()
            except InvalidTranscript as err:
                self.exception_message += "{0}\n".format(err)
                to_remove.add(tid)
            except Exception as err:
                print(err)
                raise
        for k in to_remove:
            del self.transcripts[k]
        __new_start = min(_.start for _ in self)

        if __new_start != self.start:
            self.logger.warning("Resetting the start for %s from %d to %d",
                                self.id, self.start, __new_start)
            self.start = __new_start

        __new_end = max(_.end for _ in self)
        if __new_end != self.end:
            self.logger.warning("Resetting the end for %s from %d to %d",
                                self.id, self.end, __new_end)
            self.end = __new_end

    def as_dict(self):

        """
        Method to transform the gene object into a JSON-friendly representation.
        :return:
        """

        state = dict()
        for key in ["chrom", "source", "start", "end", "strand", "id"]:
            state[key] = getattr(self, key)

        state["transcripts"] = dict.fromkeys(self.transcripts.keys())

        for tid in state["transcripts"]:
            state["transcripts"][tid] = self.transcripts[tid].as_dict()

        return state

    def load_dict(self, state, exclude_utr=False, protein_coding=False):

        for key in ["chrom", "source", "start", "end", "strand", "id"]:
            setattr(self, key, state[key])

        for tid, tvalues in state["transcripts"].items():
            transcript = Transcript()
            transcript.load_dict(tvalues)
            transcript.finalize()
            if protein_coding is True and transcript.is_coding is False:
                print("{0} is non coding ({1}, {2})".format(transcript.id,
                                                       transcript.combined_cds,
                                                       transcript.segments))
                continue
            if exclude_utr is True:
                has_utrs = (transcript.utr_length > 0)
                transcript.remove_utrs()
                if has_utrs is True and (transcript.utr_length > 0):
                    raise AssertionError("Failed to remove the UTRs!")
            self.transcripts[tid] = transcript

        return

    def remove(self, tid: str):
        """

        :param tid: name of the transcript to remove.

        This method will remove a transcript from the container, and recalculate the
         necessary instance attributes.

        """

        del self.transcripts[tid]
        if len(self.transcripts) == 0:
            self.end = None
            self.start = None
            self.chrom = None
        self.start = min(self.transcripts[tid].start for tid in self.transcripts)
        self.end = max(self.transcripts[tid].end for tid in self.transcripts)

    def __repr__(self):
        return " ".join(self.transcripts.keys())

    def __str__(self):
        return self.format("gff3")

    def __iter__(self) -> Transcript:
        """Iterate over the transcripts attached to the gene."""
        return iter(self.transcripts.values())

    def __len__(self) -> int:
        return len(self.transcripts)

    def __getstate__(self):
        state = self.__dict__
        state["logger"] = None
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)
        self.set_logger(None)

    def __lt__(self, other):
        if self.chrom != other.chrom:
            return self.chrom < other.chrom
        else:
            if self.start != other.start:
                return self.start < other.start
            elif self.end != other.end:
                return self.end < other.end
            else:
                return self.strand < other.strand

    def __eq__(self, other):
        if self.chrom == other.chrom and self.start == other.start and \
                self.end == other.end and self.strand == other.strand:
            return True
        return False

    def format(self, format_name):

        if format_name not in ("gff", "gtf", "gff3"):
            raise ValueError(
                "Invalid format: {0}. Accepted formats: gff/gff3 (equivalent), gtf".format(
                    format_name))

        self.finalize()  # Necessary to sort the exons
        lines = []
        if format_name != "gtf":
            line = GffLine(None)
            for attr in ["chrom",
                         "source",
                         "start",
                         "end",
                         "strand"]:
                setattr(line, attr, getattr(self, attr))

            line.feature = "gene"
            line.id = self.id
            assert line.id is not None
            lines.append(str(line))

        for tid, transcript in sorted(self.transcripts.items(), key=operator.itemgetter(1)):
            lines.append(transcript.format(format_name))

        return "\n".join(lines)

    @property
    def monoexonic(self):
        """
        Boolean property. False if at least one transcript is multiexonic,
        True otherwise.
        :return: bool
        :rtype: bool
        """

        return any(transcript.monoexonic is False for transcript in self.transcripts.values())

    @property
    def introns(self):
        if self.__introns is None:
            self.__introns = set.union(*[_.introns for _ in self.transcripts.values()])

        return self.__introns