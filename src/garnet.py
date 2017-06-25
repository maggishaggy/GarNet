#!/usr/bin/env python3

# Core python modules
import sys
import os

# Peripheral python modules
import pickle
import logging

# Core python external libraries
import numpy as np
import pandas as pd
from statsmodels.formula.api import ols as linear_regression
from statsmodels.graphics.regressionplots import abline_plot as plot_regression

# Peripheral python external libraries
from intervaltree import IntervalTree
import jinja2

# list of public methods:
__all__ = [ "parse_garnet_file", "map_peaks", "TF_regression" ]


templateLoader = jinja2.FileSystemLoader(searchpath=".")
templateEnv = jinja2.Environment(loader=templateLoader)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setLevel(logging.INFO)
handler.setFormatter(logging.Formatter('%(asctime)s - GarNet: %(levelname)s - %(message)s', "%I:%M:%S"))
logger.addHandler(handler)


######################################## File Parsing Logic #######################################

def parse_garnet_file(filepath_or_file_object):
	"""
	Arguments:
		filepath_or_file_object (string or FILE): A filepath or FILE object

	Returns:
		dict: {chr: IntervalTree of regions}
	"""

	garnet_file = try_to_load_as_pickled_object_or_None(filepath_or_file_object)

	if garnet_file == None: sys.exit('Unable to load garnet file')

	return garnet_file


def parse_peaks_bed_file(peaks_file):
	"""
	Parse a BED file with peaks from an epigenomics assay (e.g. ATAC) into a dataframe

	Arguments:
		peaks_file (string or FILE): BED file from epigenomics assay

	Returns:
		dataframe: peaks dataframe
	"""

	peaks_fieldnames = ["chrom","chromStart","chromEnd","name","score","strand","thickStart","thickEnd","itemRgb","blockCount","blockSizes","blockStarts"]

	peaks_dataframe = pd.read_csv(peaks_file, delimiter='\t', names=peaks_fieldnames)

	peaks_dataframe.rename(index=str, columns={"chromStart":"peakStart", "chromEnd":"peakEnd", "name":"peakName", "score":"peakScore", "strand":"peakStrand"}, inplace=True)

	peaks_dataframe = peaks_dataframe[['peakName', 'chrom', 'peakStart', 'peakEnd', 'peakScore']]

	return peaks_dataframe


def parse_tabular_expression_file(expression_file):
	"""
	Parse gene expression scores from a transcriptomics assay (e.g. RNAseq) into a dataframe

	Arguments:
		expression_file (string or FILE): Two-column, tab-delimited file of gene / gene expression score

	Returns:
		dataframe: expression dataframe
	"""

	return pd.read_csv(expression_file, delimiter='\t', names=["name", "expression"])


def save_as_pickled_object(obj, directory, filename):
	"""
	This is a defensive way to write pickle.write, allowing for very large files on all platforms
	"""
	filepath = os.path.join(directory, filename)
	max_bytes = 2**31 - 1
	bytes_out = pickle.dumps(obj)
	n_bytes = sys.getsizeof(bytes_out)
	with open(filepath, 'wb') as f_out:
		for idx in range(0, n_bytes, max_bytes):
			f_out.write(bytes_out[idx:idx+max_bytes])


def try_to_load_as_pickled_object_or_None(filepath):
	"""
	This is a defensive way to write pickle.load, allowing for very large files on all platforms
	"""
	max_bytes = 2**31 - 1
	try:
		input_size = os.path.getsize(filepath)
		bytes_in = bytearray(0)
		with open(filepath, 'rb') as f_in:
			for _ in range(0, input_size, max_bytes):
				bytes_in += f_in.read(max_bytes)
		obj = pickle.loads(bytes_in)
	except:
		return None
	return obj


def output(dataframe, output_dir, filename):
	dataframe.to_csv(os.path.join(output_dir, filename), sep='\t', header=True, index=False)


######################################### Public Functions #########################################

def map_peaks(garnet_file, peaks_file_or_list_of_peaks_files):
	"""
	Find motifs and associated genes local to peaks.

	This function searches for motifs "under" peaks from an epigenomics dataset and "around" peaks for genes.
	It then returns all pairs of motifs and genes which were found local to peaks.

	Arguments:
		garnet_file (str): filepath or file object for the garnet file.
		peaks_file_or_list_of_peaks_files (str or FILE or list): filepath or file object for the peaks file, or list of such paths or objects

	Returns:
		dataframe: a dataframe with rows of transcription factor binding motifs and nearby genes
			with the restriction that these motifs and genes must have been found near a peak.
	"""

	genome = parse_garnet_file(garnet_file)

	# peaks_file_or_list_of_peaks_files is either a filepath or FILE, or a list of filepaths or FILEs.
	# Let's operate on a list in either case, so if it's a single string, put it in a list. #TODO, this will break if it's a single FILE.
	if isinstance(peaks_file_or_list_of_peaks_files, str): peaks_files = [peaks_file_or_list_of_peaks_files]
	else: peaks_files = peaks_file_or_list_of_peaks_files

	output = []

	for peaks_file in peaks_files:

		peaks = dict_of_IntervalTree_from_peak_file(peaks_file)

		peaks_with_associated_genes_and_motifs = intersection_of_dict_of_intervaltree(peaks, genome)

		motifs_and_genes = [{**motif, **gene, **peak} for peak, genes, motifs in peaks_with_associated_genes_and_motifs for gene in genes for motif in motifs]

		columns_to_output = ["chrom", "motifStart", "motifEnd", "motifID", "motifName", "motifScore", "geneName", "geneSymbol", "geneStart", "geneEnd", "peakName"]
		motifs_and_genes = pd.DataFrame.from_records(motifs_and_genes, columns=columns_to_output)

		# Should probably map type_of_peak here

		output.append(motifs_and_genes)

	GarNetDB.close()

	# conversely, if this function was passed a single file, return a single dataframe
	if len(output) == 1: output = output[0]
	return output


def TF_regression(motifs_and_genes_dataframe, expression_file, options):
	"""
	Do linear regression of the expression of genes versus the strength of the assiciated transcription factor binding motifs and report results.

	This function parses an expression file of two columns: gene symbol and expression value, and
	merges the expression profile into the motifs and genes file, resulting in information about
	transcription factor binding motifs local to genes, and those genes' expressions. We do linear
	regression, and if an output directory is provided, we output a plot for each TF and an html
	summary of the regressions.

	Arguments:
		motifs_and_genes_dataframe (dataframe): the outcome of map_known_genes_and_motifs_to_peaks
		expression_file (str or FILE): a tsv file of expression data, with geneSymbol, score columns
		options (dict): {"output_dir": string (optional)})

	Returns:
		dataframe: slope and pval of linear regfression for each transcription factor.
	"""

	expression_dataframe = parse_expression_file(expression_file)

	motifs_genes_and_expression_levels = motifs_and_genes_dataframe.merge(expression_dataframe, left_on='geneSymbol', right_on='name', how='inner')

	# the same geneSymbol might have different names but since the expression is geneSymbol-wise
	# these additional names cause bogus regression p-values. Get rid of them here.
	if 'geneSymbol' in motifs_genes_and_expression_levels.columns:
		motifs_genes_and_expression_levels.drop_duplicates(subset=['geneSymbol', 'motifID'], inplace=True)
	motifs_genes_and_expression_levels['motifScore'] = motifs_genes_and_expression_levels['motifScore'].astype(float)

	TFs_and_associated_expression_profiles = list(motifs_genes_and_expression_levels.groupby('motifName'))
	imputed_TF_features = []
	logger.info("Performing linear regression on "+str(len(TFs_and_associated_expression_profiles))+" transcription factor expression profiles...")

	for TF_name, expression_profile in TFs_and_associated_expression_profiles:

		# Occasionally there's only one gene associated with a TF, which we can't fit a line to.
		if len(expression_profile) < 5: continue

		# Ordinary Least Squares linear regression
		result = linear_regression(formula="expression ~ motifScore", data=expression_profile).fit()

		if options.get('output_dir'):
			plot = plot_regression(model_results=result, ax=expression_profile.plot(x="motifScore", y="expression", kind="scatter", grid=True))
			if not os.path.exists(options['output_dir']+'regression_plots/'): os.makedirs(options['output_dir']+'regression_plots/')
			plot.savefig(options['output_dir']+'regression_plots/' + TF_name + '.png')

		imputed_TF_features.append((TF_name, result.params['motifScore'], result.pvalues['motifScore']))

	imputed_TF_features_dataframe = pd.DataFrame(imputed_TF_features, columns=["Transcription Factor", "Slope", "P-Value"])

	# If we're supplied with an output_dir, we'll put a summary html file in there as well.
	if options.get('output_dir'):
		html_output = templateEnv.get_template("summary.jinja").render(images_dir=options['output_dir']+'regression_plots/', TFs=sorted(imputed_TF_features, key=lambda x: x[2]))
		with open(options['output_dir']+"summary.html", "w") as summary_output_file:
			summary_output_file.write(html_output)

	return imputed_TF_features_dataframes


######################################## Private Functions ########################################

def dict_of_IntervalTree_from_peak_file(peaks_file):
	"""
	Arguments:
		peaks_file (str or FILE): filepath or FILE object

	Returns:
		dict: dictionary of intervals in known genes to intervals in peaks.
	"""

	logger.info('  - Peaks file does not seem to have been generated by pickle, proceeding to parse...')
	peaks = parse_peaks_file(peaks_file)
	peaks = group_by_chromosome(peaks)
	logger.info('  - Parse complete, constructing IntervalTrees...')
	peaks = {chrom: IntervalTree_from_peaks(chromosome_peaks) for chrom, chromosome_peaks in peaks.items()}

	if output_dir:
		logger.info('  - IntervalTree construction complete, saving pickle file for next time.')
		save_as_pickled_object(peaks, output_dir, 'peaks_IntervalTree_dictionary.pickle')

	return peaks


def group_by_chromosome(dataframe):
	"""
	Arguments:
		dataframe (dataframe): Must be a dataframe with a chrom column

	Returns:
		dict: dictionary of chromosome names (e.g. 'chr1') to dataframes
	"""

	return dict(list(dataframe.groupby('chrom')))


def IntervalTree_from_peaks(peaks):
	"""
	Arguments:
		peaks (dataframe): Must be a dataframe with peakStart and peakEnd columns

	Returns:
		IntervalTree: of peaks
	"""

	intervals = zip(peaks.peakStart.values, peaks.peakEnd.values, peaks.to_dict(orient='records'))

	tree = IntervalTree.from_tuples(intervals)

	return tree


def intersection_of_dict_of_intervaltree(A, B):
	"""
	Arguments:
		A (dict): is a dictionary of {chrom (str): IntervalTree}
		B (dict): is a dictionary of {chrom (str): IntervalTree}

	Returns:
		dict: {keys shared between A and B: {intervals in A: [list of overlapping intervals in B]} }
	"""

	logger.info('Computing intersection operation of IntervalTrees for each chromosome...')

	# Keys are chromosomes. We only want to look through chromosomes where there is potential for overlap
	common_keys = set(A.keys()).intersection( set(B.keys()) )

	# In general, the below operation isn't perfectly elegant, due to the double-for:
	#   `for key in common_keys for a in A[key]`
	# The reason we use a double-for here is because we want to do one level of "flattening"
	# but we don't want to do it as a pre-processing or post-processing step. Specifically,
	# we're passed dictionaries of chrom: IntervalTree, and we'd like to return a single data
	# structure for the entire genome, not one per chromosome. The below can be read as:
	#
	#	for chromosome in chromosomes_found_in_both_datastructures:
	#		for a_interval in A[chromosome]
	#			for b_interval in B[chromosome].search(A_interval)
	#				Map a_interval to b_interval
	#
	# which can be expressed concisely in the following line of code (which is also maximally efficient)
	intersection = [(a.data, b.data) for key in common_keys for a in A[key] for b in B[key].search(a)]

	return intersection


def type_of_peak(row):
	"""
	Arguments:
		row (pd.Series): A row of data from a dataframe with peak and gene information

	Returns:
		str: a name for the relationship between the peak and the gene:
				- upstream if the start of the peak is more than 2kb above the start of the gene
				- promoter if the start of the peak is above the start of the gene
				- downstream if the start of the peak is below the start of the gene
	"""

	if row['geneStrand'] == '+':
		if -2000 >= row['peakStart'] - row['geneStart']: 	return 'upstream'
		if -2000 < row['peakStart'] - row['geneStart'] < 0: return 'promoter'
		if 0 <= row['peakStart'] - row['geneStart']: 		return 'downstream'  # a.k.a. row['peakStart'] < row['geneStart']
		return 'intergenic'
	if row['geneStrand'] == '-':
		if 2000 <= row['peakEnd'] - row['geneEnd']: 	return 'upstream'
		if 2000 > row['peakEnd'] - row['geneEnd'] > 0: 	return 'promoter'
		if 0 >= row['peakEnd'] - row['geneEnd']: 		return 'downstream'  # a.k.a. row['peakEnd'] < row['geneEnd']
		return 'intergenic'

