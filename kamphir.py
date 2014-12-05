"""
Estimate epidemic model parameters by comparing simulations to "observed" phylogeny.
"""
import os
from pylab import *

from phyloK2 import *
import random

from copy import deepcopy
import time


class Kamphir (PhyloKernel):
    """
    Derived class of PhyloKernel for estimating epidemic model parameters
    by simulating coalescent trees and comparing simulations to the reference
    tree by the tree shape kernel function.
    
    [target_tree] = Bio.Phylo tree object to fit model to.
    
    [params] = dictionary of parameter values, key = parameter name as
        recognized by colgem2 HIVmodel, value = dictionary with following:
        'value': parameter value,
        'sigma': sigma value of Gaussian proposal,
        'min': minimum parameter value (optional),
        'max': maximum parameter value (optional)
    """
    
    def __init__(self, settings, rscript='simulate.DiffRisk.R',
                 ncores=1, nreps=10, nthreads=1):
        # call base class constructor
        PhyloKernel.__init__(self)

        self.settings = deepcopy(settings)
        self.target_tree = None

        self.current = {}
        self.proposed = {}
        for k, v in self.settings.iteritems():
            self.current.update({k: v['initial']})
            self.proposed.update({k: v['initial']})

        # locations of files
        self.path_to_tree = None
        self.path_to_input_csv = '/tmp/input.csv'
        self.path_to_label_csv = '/tmp/tips.csv'
        self.path_to_output_nwk = '/tmp/output.nwk'
        self.path_to_Rscript = rscript

        self.ntips = None
        self.tip_heights = []
        self.ref_denom = None  # kernel score of target tree to itself
        self.ncores = ncores  # number of processes for rcolgem simulation
        self.nreps = nreps
        self.nthreads = nthreads  # number of processes for PhyloKernel
    
    def set_target_tree(self, path, delimiter=None, position=None):
        """
        Assign a Bio.Phylo Tree object to fit a model to.
        Parse tip dates from tree string in BEAST style.

        :param path: location of file containing Newick tree string
        :param delimiter: if set, partition tip label into tokens
        :param position: indicates which token denotes tip date
        :return: None
        """
        # TODO: If file contains more than one tree, then assign multiple trees.
        # TODO: Read states in from file.

        self.path_to_tree = path
        print 'reading in target tree from', path
        self.target_tree = Phylo.read(path, 'newick')

        tips = self.target_tree.get_terminals()
        self.ntips = len(tips)
        print 'read in', self.ntips, 'leaves'

        # parse tip heights from labels
        if delimiter is None:
            self.tip_heights = [''] * self.ntips
        else:
            maxdate = 0
            tipdates = []
            for tip in tips:
                try:
                    items = tip.name.split(delimiter)
                    tipdate = int(items[position])
                    if tipdate > maxdate:
                        maxdate = tipdate
                except:
                    print 'Warning: Failed to parse tipdate from label', tip.name
                    tipdate = None  # gets interpreted as 0
                    pass

                tipdates.append(tipdate)

            self.tip_heights = [str(maxdate-t) if t else 0 for t in tipdates]

        # analyze target tree
        self.target_tree.ladderize()
        self.normalize_tree(self.target_tree, 'mean')
        self.annotate_tree(self.target_tree)
        self.ref_denom = self.kernel(self.target_tree, self.target_tree)
    
    
    def proposal (self):
        """
        Generate a deep copy of parameters and modify one
        parameter value, given constraints (if any).
        """
        for key in self.current.iterkeys():
            self.proposed[key] = self.current[key]
        
        # which parameter to adjust in proposal
        choices = []
        for parameter in self.settings.iterkeys():
            choices.extend([parameter] * int(self.settings[parameter]['weight']))
        to_modify = random.sample(choices, 1)[0] # weighted sampling
        #to_modify = random.sample(self.proposed.keys(), 1)[0] # uniform sampling

        proposal_value = None
        current_value = self.proposed[to_modify]
        sigma = self.settings[to_modify]['sigma']
        while True:
            proposal_value = current_value + random.normalvariate(0, sigma)
            if self.settings[to_modify].has_key('min') and proposal_value < self.settings[to_modify]['min']:
                continue
            if self.settings[to_modify].has_key('max') and proposal_value > self.settings[to_modify]['max']:
                continue
            break
        self.proposed[to_modify] = proposal_value
    
    def prior (self, params):
        """
        Calculate the prior probability of a given parameter vector.
        """
        res = 1.
        for key in params.iterkeys():
            pass
            # work in progress

    def compute(self, tree, output=None):
        """
        Calculate kernel score.  Allow for MP execution.
        """
        tree.root.branch_length = 0.
        tree.ladderize()
        self.normalize_tree(tree, 'mean')
        if not hasattr(tree.root, 'production'):
            self.annotate_tree(tree)
        k = self.kernel(self.target_tree, tree)
        tree_denom = self.kernel(tree, tree)
        knorm = k / math.sqrt(self.ref_denom * tree_denom)

        if output is None:
            return knorm

        output.put(knorm)  # MP


    def simulate(self):
        """
        Estimate the mean kernel distance between the reference tree and
        trees simulated under the given model parameters.
        """
        # TODO: allow user to set arbitrary driver Rscript
        # TODO: generalize tip label annotation

        # generate input control CSV file
        handle = open(self.path_to_input_csv, 'w')
        handle.write('n.cores,%d\n' % self.ncores)  # parallel or serial execution
        handle.write('n.reps,%d\n' % self.nreps)  # number of replicates
        for item in self.proposed.iteritems():
            handle.write('%s,%f\n' % item)  # parameter name and value
        handle.close()

        # generate tip labels CSV file
        handle = open(self.path_to_label_csv, 'w')
        for i in range(self.ntips):
            handle.write('%d,%s\n' % (
                1 if i < (self.ntips*self.proposed['p']) else 2,
                self.tip_heights[i]
            ))
        handle.close()

        # external call to Rscript
        os.system('Rscript %s %s %s %s' % (self.path_to_Rscript,
                                           self.path_to_input_csv,
                                           self.path_to_label_csv,
                                           self.path_to_output_nwk))

        # retrieve trees from output file
        trees = Phylo.parse(self.path_to_output_nwk, 'newick')
        return trees

    def evaluate(self, trees=None):
        """
        Wrapper to calculate mean kernel score for a simulated set
        of trees given proposed model parameters.
        :param trees = list of Phylo Tree objects from simulations
                        in case we want to re-evaluate mean score (debugging)
        :return [mean] mean kernel score
                [trees] simulated trees (for debugging)
        """
        if trees is None:
            trees = self.simulate()  # call rcolgem

        if self.nthreads > 1:
            output = mp.Queue()

            #processes = [mp.Process(target=self.compute,
            #                        args=(trees[i], output)) for i in range(self.nthreads)]
            #map(lambda p: p.start(), processes)
            #map(lambda p: p.join(), processes)
            pool = mp.Pool(processes=self.nthreads)

            # collect results and calculate mean
            res = [output.get() for p in processes]
        else:
            # single-threaded
            res = [self.compute(tree) for tree in trees]

        try:
            mean = sum(res)/len(res)
        except:
            print res
            raise

        return mean, trees
    
    
    def abc_mcmc(self, logfile, max_steps=1e5, tol0=0.01, mintol=0.0005, decay=0.0025, skip=1):
        """
        Use Approximate Bayesian Computation to sample from posterior
        density over model parameter space, given one or more observed
        trees.
        [sigma2] = variance parameter for Gaussian RBF
                   A higher value is more permissive.
        """
        # record settings in logfile header
        logfile.write('# colgem_fitter.py log\n')
        logfile.write('# start time: %s\n' % time.ctime())
        logfile.write('# input file: %s\n' % self.path_to_tree)
        logfile.write('# annealing settings: tol0=%f, mintol=%f, decay=%f\n' % (tol0, mintol, decay))
        
        logfile.write('# proposal settings: ')
        logfile.write('\t'.join(['%s:(%f,%f); s=%f; w=%1.2f' % (k, v['min'], v['max'], v['sigma'], v['weight']) 
                                for k, v in self.settings.iteritems()]))
        logfile.write('\n')
        
        logfile.write('# initial: ')
        logfile.write(' '.join(['%s=%f' % (k, v['initial']) for k, v in self.settings.iteritems()]))
        logfile.write('\n')
        
        cur_score, _ = self.evaluate()
        step = 0
        logfile.write('\t'.join(['state', 'score', 'c1', 'c2', 'p', 'rho', 'N', 'T']))
        logfile.write('\n')

        # TODO: generalize screen and file log parameters
        while step < max_steps:
            self.proposal()  # update proposed values
            next_score, _ = self.evaluate()
            if next_score > 1.0:
                print 'ERROR: next_score (%f) greater than 1.0, dumping proposal and EXIT' % next_score
                print self.proposal()
                sys.exit()
            
            # adjust tolerance, simulated annealing
            tol = (tol0 - mintol) * exp(-1. * decay * step) + mintol
            
            ratio = exp(-2.*(1.-next_score)/tol) / exp(-2.*(1.-cur_score)/tol)
            accept_prob = min(1., ratio)
            #step_down_prob = exp(-200.*(cur_score - next_score))
            #if next_score > cur_score or random.random() < step_down_prob:
            #rbf = math.exp(-(1-next_score)**2 / sigma2)  # Gaussian radial basis function
            
            # screen log
            #print '%d\t%1.5f\t%1.5f\t%1.3f\t%1.3f\t%1.3f\t%1.5f\t%s' % (step, cur_score, next_score, 
            #    accept_prob, self.current['c1'], self.proposed['c1'], tol, time.ctime())
            to_screen = '%d\t%1.5f\t%1.5f\t' % (step, cur_score, accept_prob)
            to_screen += '\t'.join(map(lambda x: str(round(x, 5)), [
                self.current['c1'], 
                self.current['c2'], 
                self.current['p'], 
                self.current['rho'], 
                self.current['N'],
                self.current['t.end']]))
            print to_screen
            
            if random.random() < accept_prob:
                # accept proposal
                for key in self.current:
                    self.current[key] = self.proposed[key]
                cur_score = next_score
            
            if step % skip == 0:
                logfile.write('\t'.join(map(str, [step, cur_score,
                                              self.current['c1'], 
                                              self.current['c2'],
                                              self.current['p'],
                                              self.current['rho'],
                                              self.current['N'],
                                              self.current['t.end']])))
                logfile.write('\n')
            step += 1

