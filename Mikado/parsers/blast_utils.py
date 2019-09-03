#!/usr/bin/env python3

"""
This module contains generic-purpose utilities to deal with BLAST XML files.
"""

import os
import subprocess
import gzip
import multiprocessing
import io
import collections
import time
import threading
import queue
import logging
from . import HeaderError
from ..utilities.log_utils import create_null_logger
# from Bio.SearchIO.BlastIO.blast_xml import BlastXmlParser as xparser
from Bio.Blast.NCBIXML import parse as xparser
# import Bio.SearchIO
# import functools
# xparser = functools.partial(Bio.SearchIO.parse, format="blast-xml")
from ..utilities import overlap
import xml.etree.ElementTree
import numpy as np


__author__ = 'Luca Venturini'


class BlastOpener:

    def __init__(self, filename, create_parser=True):

        self.__filename = filename
        self.__handle = None
        self.__closed = False
        self.__opened = False
        self.__parser_created = False
        self.__create_parser = create_parser
        self.__enter__()

    def __create_handle(self):

        if self.__closed is True:
            raise ValueError('I/O operation on closed file.')
        if self.__opened is True:
            return self

        if isinstance(self.__filename, (gzip.GzipFile, io.TextIOWrapper)):
            self.__handle = self.__filename
            self.__filename = self.__handle.name
        elif not isinstance(self.__filename, str) or not os.path.exists(self.__filename):
            raise OSError("Non-existent file: {0}".format(self.__filename))
        else:
            if self.__filename.endswith(".gz"):
                if self.__filename.endswith(".xml.gz"):
                    self.__handle = gzip.open(self.__filename, "rt")
                elif self.__filename.endswith(".asn.gz"):
                    # I cannot seem to make it work with gzip.open
                    zcat = subprocess.Popen(["zcat", self.__filename], shell=False,
                                            stdout=subprocess.PIPE)
                    blast_formatter = subprocess.Popen(
                        ['blast_formatter', '-outfmt', '5', '-archive', '-'],
                        shell=False, stdin=zcat.stdout, stdout=subprocess.PIPE)
                    self.__handle = io.TextIOWrapper(blast_formatter.stdout, encoding="UTF-8")
                else:
                    raise ValueError("Unrecognized file format for {}".format(self.__filename))
            elif self.__filename.endswith(".xml"):
                self.__handle = open(self.__filename)
                assert self.__handle is not None
            elif self.__filename.endswith(".asn"):
                blast_formatter = subprocess.Popen(
                    ['blast_formatter', '-outfmt', '5', '-archive', self.__filename],
                    shell=False, stdout=subprocess.PIPE)
                self.__handle = io.TextIOWrapper(blast_formatter.stdout, encoding="UTF-8")
            else:
                raise ValueError("Unrecognized file format: {}".format(self.__filename))

        self.__opened = True
        assert self.__handle is not None, self.__filename

    def __enter__(self):

        try:
            self.__create_handle()
            if self.__create_parser is True and self.__parser_created is False:
                self.parser = xparser(self.__handle)
                self.__parser_created = True
            return self
        except xml.etree.ElementTree.ParseError:
            self.__closed = True

    def open(self):
        self.__enter__()

    def __exit__(self, *args):
        _ = args
        if self.__closed is False:
            self.__handle.close()
            self.__closed = True
            self.__opened = False

    def __iter__(self):
        return self

    def __next__(self):
        if self.__handle is None:
            raise ValueError("Invalid handle")
        return next(iter(self.parser))

    @property
    def handle(self):
        """Direct access to the underlying handle, for low-level access.
        This property cannot be set directly, it will be generated by the init() method of the class."""
        print(self.__handle, type(self.__handle))
        return self.__handle

    @property
    def closed(self):
        return self.__handle.closed

    @property
    def name(self):
        return self.__filename

    def close(self):
        self.__exit__()

    def read(self, *args):
        return self.__handle.read(*args)

    def sniff(self, default_header=None):

        """
        Method that either derives the default XML header for the instance (if undefined)
        or checks that the given file is compatible with it.
        :param default_header: optional default header to check for consistency
        :return: boolean (passed or not passed)
        :rtype: (bool, list, str)
        """

        if self.__opened is True:
            self.close()
            self.__closed = False
            self.__opened = False
        try:
            self.__create_handle()
        except xml.etree.ElementTree.ParseError as exc:
            return False, None, xml.etree.ElementTree.ParseError("{0} ({1})".format(exc,
                                                                                    self.__filename))

        header = []
        exc = None
        valid = True
        # assert handle is not None
        while True:
            try:
                line = next(self.__handle)
            except StopIteration:
                # Hack for empty files
                valid = False
                exc = HeaderError("Invalid header for {0}:\n\n{1}".format(
                    self.__filename,
                    "\n".join(header)
                ))
                break
            if "<Iteration>" in line:
                break
            line = line.rstrip()
            if not line:
                valid = False
                exc = HeaderError("Invalid header for {0}:\n\n{1}".format(
                    self.__filename,
                    "\n".join(header)
                ))
                break
            if len(header) > 10**3:
                exc = HeaderError("Abnormally long header ({0}) for {1}:\n\n{2}".format(
                    len(header),
                    self.__filename,
                    "\n".join(header)
                ))
                break
            header.append(line)
        if not any(iter(True if "BlastOutput" in x else False for x in header)):
            exc = HeaderError("Invalid header for {0}:\n\n{1}".format(
                self.__filename, "\n".join(header)))

        if default_header is not None and exc is None:
                checker = [header_line for header_line in header if
                           "BlastOutput_query" not in header_line]
                previous_header = [header_line for header_line in default_header if
                                   "BlastOutput_query" not in header_line]
                if checker != previous_header:
                    exc = HeaderError("BLAST XML header does not match for {0}".format(
                        self.__filename))
        elif exc is None:
            default_header = header

        if exc is not None:
            valid = False
        self.__handle.close()

        return valid, default_header, exc


def __calculate_merges(intervals: np.array):
    """
    Internal function used by merge to perform the proper merging calculation.
    :param intervals:
    :return:
    """

    if intervals.shape[0] == 1:
        return intervals

    new_intervals = np.ma.array(np.empty(intervals.shape, dtype=intervals.dtype),
                                dtype=intervals.dtype,
                                mask=True)

    pos = 0
    current = None

    for iv in intervals:
        if current is None:
            current = iv
            continue
        else:
            if overlap(current, iv, positive=False) >= 0:
                current = (min(current[0], iv[0]),
                           max(current[1], iv[1]))
            else:
                new_intervals[pos] = current
                current = iv
                pos += 1

    new_intervals[pos] = current
    new_intervals = np.array(new_intervals[~new_intervals[:, 0].mask], dtype=new_intervals.dtype)
    new_intervals = new_intervals[np.lexsort((new_intervals[:, 1], new_intervals[:, 0]))]
    return new_intervals


def merge(intervals: [(int, int)], query_length=None, offset=1):
    """
    This function is used to merge together intervals, which have to be supplied as a list
    of duplexes - (start,stop). The function will then merge together overlapping tuples and
    return a list of non-overlapping tuples.
    If the list is composed by only one element, the function returns immediately.
    :param intervals: a list of integer duplexes
    :type intervals: list

    :param query_length: pre-defined length of the feature to calculate the intervals for.
    If provided, it will verify that the total sum of the ranges is within the length.
    :param offset: offset to subtract when calculating the total length. Default 1, it can be either 0 or 1.

    :returns: merged intervals, length covered

    """

    # Assume tuple of the form (start,end)
    # Create array and sort
    offset = int(offset)
    if offset not in [0, 1]:
        raise ValueError("Invalid offset - only 0 and 1 allowed: {}".format(offset))

    try:
        intervals = np.array([sorted(_) for _ in intervals], dtype=np.int)
        if intervals.shape[1] != 2:
            raise ValueError("Invalid shape for intervals: {}".format(intervals.shape))
    except (TypeError, ValueError):
        raise TypeError("Invalid array for intervals: {}".format(intervals))

    intervals = intervals[np.lexsort((intervals[:,1], intervals[:,0]))]
    intervals = __calculate_merges(intervals)
    total_length_covered = int(abs(intervals[:,1] - intervals[:,0] + offset).sum())

    if not query_length:
        query_length = int(abs(intervals[:,1].max() - intervals[:,0].min() + offset))

    if query_length and total_length_covered > query_length:
        raise AssertionError("Something went wrong, original length {}, total length {}".format(
            query_length, total_length_covered))

    return [(int(_[0]), int(_[1])) for _ in intervals], total_length_covered
