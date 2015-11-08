#Mikado - pick your transcript: a pipeline to determine and select the best RNA-Seq prediction

##Overview

Mikado is a lightweight Python3 pipeline whose purpose is to facilitate the identification
of expressed loci from RNA-Seq data * and to select the best models in each locus.

The logic of the pipeline is as follows:

1. In a first step, the annotation (provided in GTF/GFF3 format) is parsed to locate *superloci* of overlapping features
on the **same strand**.
2. The superloci are divided into different *subloci*, each of which is defined as follows:

    * For multiexonic transcripts, to belong to the same sublocus they must share at least a splicing junction
    (i.e. an intron)
    * For monoexonic transcripts, they must overlap for at least one base pair
    * All subloci must contain either only multiexonic or only monoexonic transcripts
3. In each sublocus, the pipeline selects the best transcript according to a user-defined prioritization scheme.
4. The resulting *monosubloci* are merged together, if applicable, into *monosubloci_holders*
5. The best non-overlapping transcripts are selected, in order to define the *loci* contained inside the superlocus.

    * At this stage, monoexonic and multiexonic transcript are checked for overlaps
    * Moreover, two multiexonic transcripts are considered to belong to the same locus if they share a splice *site*
    (not junction)

The resulting, cleaned file does contain therefore only one transcript per locus.
The criteria used to select the "*best*" transcript are left to the user's discretion.

*****

## Installation

To install the package, just run 

```bash
python3 setup.py install
```

at the command prompt. You might need either administrator privileges or to create a local python
installation directory (in the latter case, said directory must be added to your PYTHONPATH variable).

The package provides a (not yet comprehensive) suite of tests under the "test" directory. You can verify a successful
installation by running

```bash
python3 setup.py nosetests
```

Finally, the folder "sample_data" contains a toy example and a toy pipeline implmeneted using Snakemake. If the
installation has been successful, the command

```bash
snakemake complete
```

should run to completion and produce a compare.stats file, indicating that Mikado completed and was able to retrieve
2 perfect intron chain matches for the reference region. Please note that the toy example is dependent on the
presence of BLAST+ on the system.

## Usage

The suite is invoked using the command "mikado.py" followed by the desired subcommand. Detailed help, both for the
suite and for any subcommand, can be invoked using the -h/--help flag on the command line.

The main program is composed of three major steps:

1. prepare
2. serialise
3. pick

### Prepare

The prepare component has the purpose of taking as input a GTF created from one or multiple alignment programs, and it
will perform the following operations:

1. Sort the transcripts, add a transcript feature line if needed.
2. Strip the strand from monoexonic transcripts, if required
3. Check the strand of the junctions and either remove or flag those transcripts that contain junctions on both strands.

Typical invocation of the step:

```bash
mikado.py prepare --fasta <genome file> -t <threads> <gtf> <out>
```

At the moment, *prepare only supports GTF files without CDS information*. GFF files can be used as input after sorting
with genometools' with the following sample command line:

```bash
gt gff3 -sort -tidy -f -o <out gff> gff
```

### Serialise

This step is used to create the SQL database which stores the data for the pick component.

Typical invocation:

```bash
mikado.py serialise --orfs <transdecoder ORFs BED12> --transcript_fasta <transdecoder FASTA input> \
  --target_seqs <BLAST database> --xml <BLAST XML (outfmt 5)> \
  --junctions <reliable junctions BED> --genome_fai <genome FAI>
  --json-conf <configuration in JSON/YAML>
```

### Pick

This is the step where Mikado finally examines the prediction and chooses between different transcripts.

The configuration of the run is contained in a JSON/YAML file, with additional parameters - typically the scoring -
contained in a separate configuration file. Most of the options on the command line modify at runtime the values
contained in the JSON file, so be cautious.
Notice that the GFF/GTF *MUST* be sorted for the pipeline to function properly.

Typical invocation:

```bash
mikado.py pick -p <processors> --json-conf <configuration> --loci_out <output file> <prepared GTF/GFF>
```

### Compare

A separate component of the Mikado suite, compare, is capable of comparing a reference file vs. a prediction file and
calculate their concordance both at the global and at the local level. It has been heavily inspired by both Cuffcompare
and ParsEval.

It can be invoked in the following way:

```bash
mikado.py compare -p <prediction> -r <reference> -o <output> -l <log, otherwise STDERR>
```

The utility is quite light and fast, although not at the level of Cuffcompare, but it is considerably more detailed than
the predecessor.

### Utilities

Mikado also provides some utilities related to the management of GFF/GTF files. These can be invoked as subcommands of
mikado.py util.

#### Stats

This utility is capable of generating detailed statistics on the content of an annotation file. Typical usage:

```bash
mikado.py util stats <gff> <out>
```

#### Awk_Gtf

This utility allows to retrieve all the features contained in a slice of a GTF file. Compared with a simple grep/awk, it
is cognizant of the tree relationships and will avoid e.g. retrieving only some exons of a transcript instead of the
whole transcript object.

Usage:
```bash
mikado.py util awk_gtf --chrom <chrom> --start <start> --end <end> <gtf> <out>
```

Start/end parameters are optional, as the output file.

#### Grep

This utility allows to retrieve all transcripts/genes specified in a tab-separated input file.

```bash
mikado.py util grep <ids> <gff/gtf> <out>
```

#### Trim

This utility allows to remove the trailing ends of gene models until they are at most N bps long. Shorter terminal
exons are left untouched. It accepts both GTFs and GFFs.
Typical usage:

```bash
mikado.py util trim -ml <maximum length> <gff/gtf> <out>
```

*****

##Implementation
The pipeline is implemented in Python3, and it does require the following libraries:
	* SQLAlchemy, for using the internal databases
	* NetworkX, as it provides the methods necessary to describe and serialise transcript loci as Undirected Acyclic Graphs.
	* BioPython, for reading BLAST and FASTA data files
	* PyYaml, to use YAML configuration files.
	
The following packages are recommended for additional functionalities but not necessary:
	* Numpy: optionally used by the gff_stats utility
	* psycopg2 and mysqlclient (v. >= 1.3.6), for using PosGreSQL or MySQL databases instead of default SQLite

It has been tested on UNIX systems, using Python 3.4.3. The package has been written for Python3 and there are no
plans to backport it to Python2 at the moment.
Beware that Cygwin at the time of this writing does not provide a version of Python3 more recent than 3.2.3,
and it is therefore unsuitable for using Mikado.

###Implementation details

In a first step, transcripts are recovered from the input files and inserted into a *superlocus* object, irrespective
of strand.
Superloci objects are then split into superloci stranded objects, according to their strand (method "split_by_strand",
which returns an iterator).
In each stranded superlocus, transcripts are grouped into subloci according to the logic outlined in the "Overview" paragraph,
using the "define_subloci" method.
At this point, the superlocus object uses the "define_monosubloci" method to choose the best transcript for each sublocus.
In each sublocus, transcripts are analyzed to give them a score according to the specifications in the JSON configuration
(see "Scoring transcripts" section), using the "calculate_scores" method.
The JSON configuration might also contain a "requirements" field. Any transcript not passing these requirements will
be given a score of 0.
The "best" transcripts are used to create "monosubloci" objects. All the monosubloci are gathered into
monosubloci_holder objects, and the process of scoring and selection begins again, using the "define_loci" method.
Surviving transcripts are used to define the loci which will be the final output.

All "loci" objects are defined as implementations of the "abstractlocus" class.
As such, each of them must implement the following methods:
* *is_intersecting*		This method is used to define when two transcripts are intersecting. It varies from locus to
locus, so it must be specified by each of the objects.
* __init__
* __str__

The abstractlocus class provides many methods (both class and instance ones) that are used throughout the program. 

##Input files
The pipeline can use as input files any properly GTF or GFF3 file. The only requirements are:
* GTF files must have a "transcript/mRNA" line for each transcript present in the annotation
  * Standard GTF files, which have only CDS/exon/start/stop features, are not interpreted correctly
* All files must be sorted by chromosome, start and stop.
  * A utility called "sort_gtf.py", present in the util folder, can be used to sort GTFs.
  For GFF3s, our recommendation is to use [GenomeTools](http://genometools.org/), with e.g. the following command line:
  ```bash
  gt gff3 -sort -retainids -addids no -tidy -force -o <output> <input>
  ```

Additionally, the pipeline can also use the output of coding potential programs such as [TransDecoder](https://transdecoder.github.io/ "TransDecoder (Find Coding Regions Within Transcripts)").
In order to give this type of data to the pipeline, the coding regions should be encoded into a BED12
file, with the "thickStart/thickEnd" fields (7 and 8) indicating the start and stop of the CDS.
As in transdecoder, it is expected that the coding potential has been defined using oriented transcript sequences.
Any multiexonic CDS found on the negative transcript strand will be therefore ignored, as it happens in TD.

##Output files
The pipeline can produce output files at the following stages:
* Definition of subloci
* Definition of monosubloci
* Definition of loci

At each stage it is possible to obtain both a a GFF of the transcripts currently considered, and all the statistics
of the remaining transcripts.
GFFs can be printed using a simple "print" call, while the metrics have to be retrieved as dictionaries using the
"print_metrics" file. These dictionaries can be written out using the DictWriter method from the standard csv library.

If the "purge" option is selected, all transcripts which do not pass the minimum requirements will be excluded.
This option can factively lead to the loss of whole loci, if they contain only low-quality transcripts.

GTF output is not supported, as the output is more hierarchical than what is supportable in a GTF file.

*****

##Scoring transcripts

###Available metrics
Metrics are defined into a text file, "metrics.txt". Each of them is defined as a property inside the "transcript" class.
The documentation for each of them can be generated with the utility "generate_metric_docs.py" inside the util folder.  



###Defining new metrics
For the program to be able to use a novel metric, it must be implemented as a *Metric* inside the
transcript class. The "Metric" class is an alias of "property", and therefore metrics are coded as properties would:

```
@Metric
def my_metric(self):
	return self.__my_metric

@my_metric.setter
def my_metric(self,value):
	self.__my_metric = value
``` 

If the metric is relative to other transcripts in the locus (e.g. retained introns),
it must still be implemented as above, but its value must be set by the "calculate_metrics" method in the
sublocus/monosublocus_holder class.

##Scoring configuration
At each selection stage, the best transcript for the span is selected according to user-defined criteria, supplied
through a JSON file.Each metric must be specified in the "parameters" head field of the JSON configuration.
For each parameter, it is possible to specify the following:

  *"multiplier"		A number by which the metrics will be multiplied to get the final score.
  *"rescaling"		This key controls the rescaling performed to calculate the score:
  *"max"
  *"min"
  *"target"
    
Each parameter will have a score assigned which varies from 0 to 1. If the "target" rescaling is selected,
it is *mandatory* to specify a "value" keyword. For details, see [RAMPART supporting material (section 2, page3)](http://bioinformatics.oxfordjournals.org/content/suppl/2015/01/29/btv056.DC1/supplementary.pdf)

Moreover, for each parameter it is possible to configure a "filter", i.e. boundaries after which the score for this parameter is set automatically to 0 (e.g. a 3'UTR total length over 2.5 kbps). Each "filter" subfield must contain the following:

* "operator": one of
  * "eq": equal to
  * "ne": different from
  * "gt": greater than
  * "ge": greater than or equal to
  * "lt": less than
  * "le": less than or equal to
  * "in": value in array of valid values
  * "not in": value not in array of invalid values

The comparisons are always made against the reference value.
