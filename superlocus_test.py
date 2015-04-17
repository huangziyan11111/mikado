#!/usr/bin/env python3

from superlocus import superlocus
from transcript import transcript
import sys,argparse
from myRecords.GFF import GFF3
from myRecords.GTF import GTF

def add_transcripts(transcript_instance, sloci):
    if transcript_instance is None:
        return sloci
    transcript_instance.finalize()
    foundLocus=False
    if len(sloci)==0 or transcript_instance.start>sloci[-1].end:
        slocus=superlocus(transcript_instance)
        sloci.append(slocus)
    else:
        for sindex in reversed(range(len(sloci))):
            slocus=sloci[sindex]
            if superlocus.in_locus(slocus, transcript_instance) is True:
                sloci[sindex].add_transcript_to_locus(transcript_instance)
                foundLocus=True
                break
        if foundLocus is False:
            slocus=superlocus(transcript_instance)
            sloci.append(slocus)
    return sloci 

def main():
    
    parser=argparse.ArgumentParser("Quick test utility for the superlocus classes.")
    parser.add_argument("gff", type=argparse.FileType("r"))
    parser.add_argument("out", type=argparse.FileType("w"), default=sys.stdout, nargs="?")
    args=parser.parse_args()

    sloci=[]
    
    #currentLocus=None
    currentTranscript=None
    currentChrom=None
    
    if args.gff.name[-3:]=="gtf": rower=GTF(args.gff)
    else: rower=GFF3(args.gff)
    
    for row in rower:
        
        if row.header is True: continue
        if row.chrom!=currentChrom:
            if currentChrom is not None:
                sloci=add_transcripts(currentTranscript, sloci)
                for slocus in sloci:
                    print(slocus, file=args.out)
            currentChrom=row.chrom
            currentTranscript=None
            sloci=[]
        if row.feature=="transcript" or "RNA" in row.feature.upper() or row.feature=="mRNA":
            sloci=add_transcripts(currentTranscript, sloci)
            currentTranscript=transcript(row)
        elif row.feature in ("exon", "CDS"):
            currentTranscript.addExon(row)
        else:
            continue
        
    sloci=add_transcripts(currentTranscript, sloci)
    for slocus in sloci:
        print(slocus, file=args.out)

if __name__=="__main__":
    main()