import os
import sys
import argparse
import time

from Bio import SeqIO

from antismash.common import module_results


PYTHON_VERSION = sys.version_info[0]
if PYTHON_VERSION == 2:
    import ConfigParser as configparser
elif PYTHON_VERSION == 3:
    import configparser

from subprocess import call, Popen, PIPE

from multiprocessing import Process, Queue

# Adapted version to be integrated into antiSMASH

def parse_genbank(path,out={}):
    if type(path) == list:
        for item in path:
            out = parse_genbank(item,out)
    else:
        all_seqs = open_genbank(path)
        seq_dict = gbk_to_dict(all_seqs)
        out.update(seq_dict)
    return out

def open_genbank(in_path):
    all_seqs = []
    for seq_record in SeqIO.parse(in_path, "genbank"):
        all_seqs.append(seq_record)
    return(all_seqs)

def gbk_to_dict(all_seqs):
    seq_dict = {}
    for seq in all_seqs:
        contig_id = seq.id
        if seq.id == 'unknown':
            contig_id = seq.name
        for feature in seq.features:
            if feature.type == 'CDS':
                name = contig_id
                name += '_%s-%s' %(feature.location.start,feature.location.end)
                for item in ['gene','locus_tag','protein_id']:
                    if item in feature.qualifiers:
                        name += '_%s' %(feature.qualifiers[item][0])
                if 'translation' in feature.qualifiers:
                    seq = feature.qualifiers['translation'][0]
                else:
                    seq = feature.extract(seq).translate()
                seq_dict[name] = seq
    return seq_dict

def parse_fasta(path,out=None):
    if out == None:
        out = {}
    if type(path) == list:
        print('Parsing multiple')
        for item in path:
            out = parse_fasta(item,out)
    else:
        infile = open(path)
        name = False
        for line in infile:
            line = line.strip()
            if ">" in line:
                if name and name not in out:
                    out[name] = seq
                name_start = line.find(">")
                name = line[name_start+1:]
                seq = ""
            else:
                seq += line
        if name not in out:
            out[name] = seq
    return(out)
                
def write_fasta(all_groups):
    for group in all_groups:
        with open(group.fasta_file,'w') as handle:
            handle.write(group.fasta)
            
def write_fasta_single(settings,groups):
    if not hasattr(settings,'fasta_file_all'):
        settings.fasta_file_all = fasta_file_all = os.path.join(settings.fasta_folder,'fasta_all.fasta')
        settings.logger.log('Rewriting fasta',1)
        # Write all fasta files
        with open(fasta_file_all,'w') as handle:
            for group in groups:
                handle.write(group.fasta)
            
def dict_to_fasta(d,f=False,mode='w'):
    s = ''
    for key in d:
        seq = d[key]
        s += '>%s\n%s\n' %(key,seq)
    if f:
        with open(f,mode) as handle:
            handle.write(s)
    return s
            
def expand_alignment(all_groups,settings,resubmit=False):
    settings.logger.log('Expanding alignments',1)
    db_path = settings.expand_database_path
    if resubmit:
        nr_iter = settings.resubmit_initial_hhblits_iter
    else:
        nr_iter = settings.hhbllits_iter
    for group in all_groups:
        infile = group.fasta_file
        if not os.path.isfile(group.exp_alignment_file) or settings.overwrite_hhblits:
            a3m_hhblits(infile,group.exp_alignment_file,db_path,settings,settings.cores,n=nr_iter)

def a3m_hhblits(inf,outf,db,settings,threads=1,n=3):
    clean = outf.rpartition('.')[0]
    dumpfile = clean + '.hhr'
    commands = ['hhblits','-cpu',str(threads),'-d',db,'-i',inf,'-oa3m',outf,'-o',dumpfile,'-v','0','-n',str(n)]
    settings.logger.log(' '.join(commands),2)
    call(commands)

def add_ss(group,settings,resubmit=False,remove=False):
    # Only if the alignment was also expanded
    if resubmit:
        infile = group.RRE_expalign_file
    else:
        infile = group.exp_alignment_file
    # Count the number of lines of entries
    # Only add ss if the a3m doesn't consist of a single sequence
    with open(infile) as handle:
        text = handle.read()
    if text.count('>') == 1:
        settings.logger.log('Only one entry found in infile %s. Not adding secondary structure' %(infile),2)
    else:
        newfile = infile[:-4] + '_ss.a3m'
        if not os.path.isfile(newfile):
            cmds = ['addss.pl',infile,newfile,'-a3m']
            settings.logger.log(' '.join(cmds),2)
            p = Popen(cmds,stdout=PIPE,stderr=PIPE)
            p.wait()
            stdout,stderr = p.communicate()
            if type(stdout) == bytes:
                stdout = str(stdout, 'utf-8')
            if type(stderr) == bytes:
                stderr = str(stderr, 'utf-8')
            settings.logger.log(stdout,2)
            settings.logger.log(stderr,2)
        if resubmit:
            group.RRE_expalign_file = newfile
            if remove:
                os.remove(infile)
        else:
            group.exp_alignment_file = newfile
            if remove:
                os.remove(infile)

def add_all_ss(groups,settings,resubmit=False):
    settings.logger.log('Adding secondary structure',1)
    for group in groups:
        add_ss(group,settings,resubmit=resubmit)

def hhsearch_all(all_groups,settings):
    settings.logger.log('Running hhsearch',1)
    db_path = settings.rre_database_path
    for group in all_groups:
        if settings.rrefinder_primary_mode == 'hhpred':
            infile = group.exp_alignment_file
        else:
            infile = group.fasta_file
        outfile = group.results_file
        if not os.path.isfile(outfile) or settings.overwrite_hhblits:
            hhsearch(settings,infile,outfile,db_path,settings.cores)

def hhsearch(settings,inf,outf,db,threads):
    commands = ['hhsearch','-cpu',str(threads),'-d',db,'-i',inf,'-o',outf, '-v','0']
    settings.logger.log(' '.join(commands),2)
    call(commands)

def find_RRE_hits(results,targets,min_prob = 50.0,min_len = 0):
    sign_hits = {}
    for res in results:
        for target in targets:
            if res.startswith(target):
                prob = results[res][0]
                start,end = (int(i) for i in results[res][6].split('-'))
                if prob >= min_prob and (end-start) > min_len:
                    sign_hits[res] = results[res]
                    break
    return sign_hits

def read_hhr(f):
    results = {}
    with open(f) as inf:
        # skip the header
        for _ in range(9):
            l = inf.readline()
        # Now start reading
        for l in inf:
            if l == '\n':
                return results
            name = l[4:35].strip(' ')
            rest = l[35:]
            tabs = rest.strip('\n').split(' ')
            tabs = [t for t in tabs if t != '']
            prob,ev,pv,score,ss = [float(i) for i in tabs[0:5]]
            colums = int(tabs[5])
            query_hmm = tabs[6]
            template_hmm = tabs[7]
            if name not in results:
                results[name] = [prob,ev,pv,score,ss,colums,query_hmm,template_hmm]  
    return results
    
def convert_cluster(cluster,settings):
    # Convert the antiSMASH cluster to a GeneObject
    all_genes = []
    skipped = []
    
    for cdsfeature in cluster.cds_children:
        org_name = cdsfeature.get_name()
        # Make sure it contains no illegal characters
        clean_name = org_name
        clean_name.replace(' ','')
        for char in '();:<>|/\\"':
            clean_name = clean_name.replace(char,'')
        clean_name = clean_name.replace("'",'')
        seq = cdsfeature.translation
        
        if settings.max_length_prot and len(seq) > settings.max_length_prot:
            settings.logger.log('Not analyzing gene %s (too long)' %(clean_name),2)
            skipped.append(clean_name)
            continue

        fasta_file = os.path.join(settings.fasta_folder,clean_name + '.fasta')
        results_file = os.path.join(settings.results_folder, '%s.hhr' %(clean_name))
        fasta = '>%s\n%s\n' %(clean_name,seq)
        gene_obj = GeneObject()
        gene_obj.setattrs(fasta_file=fasta_file,results_file=results_file,fasta=fasta,name=clean_name,group=False,org_name=org_name)
        if settings.mode != 'rrefam' and settings.rrefinder_primary_mode == 'hhpred':
            exp_alignment_file = os.path.join(settings.fasta_folder,'%s_expalign.a3m' %clean_name)
            gene_obj.exp_alignment_file = exp_alignment_file
        all_genes.append(gene_obj)
        
        if settings.resubmit:
            # Set extra paths for the files
            RRE_fasta_file = os.path.join(settings.fasta_folder,clean_name+'_RRE.fasta')
            RRE_results_file = os.path.join(settings.results_folder,clean_name+'_RRE.hhr')
            RRE_expalign_file = os.path.join(settings.fasta_folder,clean_name+'_RRE_expalign.a3m')
            gene_obj.setattrs(RRE_fasta_file=RRE_fasta_file,RRE_results_file=RRE_results_file,RRE_expalign_file=RRE_expalign_file)
    
    return all_genes,skipped
        
def determine_RRE_locations(group,settings,mode,resubmit=False):

    def set_loc(group,dict_key,keyword,RRE_locations):
        if dict_key in RRE_locations:
            return RRE_locations
        if hasattr(group,keyword):
            hittype,RRE_data = getattr(group,keyword)
        else:
            return RRE_locations
        locs = {}
        for hit,hit_data in RRE_data.items():
            if hittype == 'hhpred':
                start,end = [int(i) for i in hit_data[6].split('-')]
            else:
                start,end = hit_data[0:2]
            locs[hit] = int(start),int(end)
        RRE_locations[dict_key] = locs
        return RRE_locations
    
    if hasattr(group,'RRE_locations'):
        RRE_locations = group.RRE_locations
    else:
        RRE_locations = {}
    # rrefinder locations
    if mode == 'rrefinder' or mode == 'both':
        if resubmit:
            RRE_locations = set_loc(group,'RREfinder_resubmit','RRE_resubmit_data',RRE_locations)
        else:
            RRE_locations = set_loc(group,'RREfinder','RRE_data',RRE_locations)

    # rrefam locations
    if mode == 'rrefam' or mode == 'both':
        RRE_locations = set_loc(group,'RREfam','RREfam_data',RRE_locations)

    group.RRE_locations = RRE_locations
        
def extract_RRE(group,settings):
    # print(group.name)
    min_start = 10e6
    max_end = 0
    for hit,data in group.RRE_locations['RREfinder'].items():
        start = data[0]
        end = data[1]
        if start < min_start:
            min_start = start
        if end > max_end:
            max_end = end
                
    fasta_out = {}
    parts = group.fasta.split('\n')
    fasta = {parts[0]:parts[1]}

    for name,seq in fasta.items():
        # If multiple genes are part of this group, extract each RRE and make a new alignment from them
        newname = '%s_RRE' %(group.name)
        RRE_start = max(0,min_start-settings.extra_left)
        RRE_end = max_end+settings.extra_right
        RRE_part = seq[RRE_start:RRE_end]
        fasta_out[newname] = RRE_part
    dict_to_fasta(fasta_out,group.RRE_fasta_file)
    group.RRE_extracted_loc = RRE_start,RRE_end
    
def resubmit_group(group,RRE_targets,settings,cores):
    # Determine RRE location
    determine_RRE_locations(group,settings,'rrefinder',resubmit=False)
    # Extract the RRE
    extract_RRE(group,settings)
    infile = group.RRE_fasta_file
    # Expand the alignment
    db_path = settings.resubmit_database
    if not os.path.isfile(group.RRE_expalign_file):
        a3m_hhblits(infile,group.RRE_expalign_file,db_path,settings,cores,n=3)
    if settings.addss == 2 or settings.addss == 3:
        add_ss(group,settings,resubmit=True)
    # Run HHsearch
    db_path = settings.rre_database_path
    if not os.path.isfile(group.RRE_results_file):
        hhsearch(settings,group.RRE_expalign_file,group.RRE_results_file,db_path,settings.cores)
    # Parse the results
    settings.logger.log('Parsing results',2)
    parse_hhpred_res(group,RRE_targets,settings,resubmit=True)
    
def parse_hhpred_res(group,RRE_targets,settings,resubmit=False):
    if resubmit:
        results = read_hhr(group.RRE_results_file)
        RRE_hits = find_RRE_hits(results,RRE_targets,min_prob=settings.min_prob,min_len = settings.min_len_alignment)
        settings.logger.log('Found %i RRE hits' %len(RRE_hits),2)
        if len(RRE_hits) > 0:
            group.RRE_resubmit_hit = True
            group.RRE_resubmit_data = ['hhpred',RRE_hits]
            # The hit coordinates indicate where within the extracted sequence the hit was. 
            # These need to be adjusted to coordinates within the protein.
            adjust_RRE_loc(group,settings)
        else:
            group.RRE_resubmit_hit = False
    else:
        if settings.resubmit:
            prob = settings.resubmit_initial_prob
        else:
            prob = settings.min_prob
        results = read_hhr(group.results_file)
        RRE_hits = find_RRE_hits(results,RRE_targets,min_prob=prob,min_len = settings.min_len_alignment)
        if len(RRE_hits) > 0:
            group.RRE_data = ['hhpred',RRE_hits]
            group.RRE_hit = True
        else:
            group.RRE_hit = False
    
def adjust_RRE_loc(group,settings):
    hittype, RRE_data = group.RRE_resubmit_data
#    print(group.name)
    for hit in RRE_data:
        data = RRE_data[hit]
        org_start,org_end = [int(i) for i in data[6].split('-')]
#        print('Original start: %s\tOriginal end: %s' %(org_start,org_end))
#        print('Extracted loc: %s\t%s' %(group.RRE_extracted_loc[0],group.RRE_extracted_loc[1]))
        new_start = group.RRE_extracted_loc[0] + org_start
        new_end = group.RRE_extracted_loc[0] + org_end
        new_coords = '%s-%s' %(new_start,new_end)
#        print('New coords: %s' %new_coords)
        data[6] = new_coords
    
    
def parse_all_RREs(groups,RRE_targets,settings,resubmit=False):
    print('Parsing results')
    for group in groups:
        parse_hhpred_res(group,RRE_targets,settings,resubmit)

def resubmit_all(groups,RRE_targets,settings):
    settings.logger.log('Resubmitting %i found RREs' %(len([i for i in groups if i.RRE_hit])),1)
    for group in groups:
        if group.RRE_hit:
            resubmit_group(group,RRE_targets,settings,settings.cores)

def parse_hmm_domtbl(p):
    # Parse per domain found
    # Sort out overlap later
    with open(p) as f:
        outd = {}
        for _ in range(3):
            l = f.readline()
        for l in f:
            if l.startswith('#'):
                continue
            tabs = l.strip().split(' ')
            tabs = [tab for tab in tabs if tab != '']
            protein_name = tabs[3]
            domain_found = tabs[0]
#                print protein_name,domain_found
            if '.' in domain_found:
                domain_found = domain_found.rpartition('.')[0]
            bitscore = float(tabs[13])
            domain_evalue = tabs[12]
            if 'e' in domain_evalue:
                parts = domain_evalue.split('e')
#                    print domain_evalue,parts
                domain_evalue = float(parts[0])*10**int(parts[1])
            else:
                domain_evalue = float(domain_evalue)
#                print(tabs[17],tabs[18])
            seq_start = int(tabs[17])
            seq_end = int(tabs[18])
            if seq_start > seq_end:
#                print 'Seq start after end: %s' %protein_name
                pass
            if protein_name not in outd:
                outd[protein_name] = []
            outd[protein_name].append([domain_found,seq_start,seq_end,domain_evalue,bitscore])
    return outd
    
def parse_hmm_domtbl_hmmsearch(p):
    # Parse per domain found
    # Sort out overlap later
    with open(p) as f:
        outd = {}
        for _ in range(3):
            l = f.readline()
        for l in f:
            if l.startswith('#'):
                continue
            tabs = l.strip().split(' ')
            tabs = [tab for tab in tabs if tab != '']
            protein_name = tabs[0]
            domain_found = tabs[3]
#                print protein_name,domain_found
            if '.' in domain_found:
                domain_found = domain_found.rpartition('.')[0]
            bitscore = float(tabs[13])
            domain_evalue = tabs[12]
            if 'e' in domain_evalue:
                parts = domain_evalue.split('e')
#                    print domain_evalue,parts
                domain_evalue = float(parts[0])*10**int(parts[1])
            else:
                domain_evalue = float(domain_evalue)
#                print(tabs[17],tabs[18])
            seq_start = int(tabs[17])
            seq_end = int(tabs[18])
            if seq_start > seq_end:
#                print 'Seq start after end: %s' %protein_name
                pass
            if protein_name not in outd:
                outd[protein_name] = []
            outd[protein_name].append([domain_found,seq_start,seq_end,domain_evalue,bitscore])
    return outd

def find_RRE_hits_hmm(groups,results,cutoff,min_len=0,keyword='RRE'):
    for group in groups:
        if group.name in results:
            domains_found = results[group.name]
            # Get the domain with the highest bitscore
            highest_bitscore = 0
            best_domain = None
            for domain in domains_found:
                length = domain[2] - domain[1]
                if length < min_len:
                    continue
                bitscore = float(domain[-1])
                if bitscore > highest_bitscore:
                    highest_bitscore = bitscore
                    best_domain = domain
            if best_domain and highest_bitscore >= cutoff:
                setattr(group,'%s_hit' %keyword,True)
                setattr(group,'%s_data' %keyword, ['hmm',{best_domain[0]:best_domain[1:]}])
            else:
                setattr(group,'%s_hit' %keyword,False)
        else:
            setattr(group,'%s_hit' %keyword,False)
   
def find_RREfam_hits(groups,results,cutoff,min_len=0,keyword='RREfam'):
    for group in groups:
        if group.name in results:
            domains_found = results[group.name]
            # Get all the domains if they're longer than the minimum length, but only one hit per hmm
            all_domains = {}
            for domain in domains_found:
                length = domain[2] - domain[1]
                if length < min_len:
                    continue
                bitscore = domain[4]
                if bitscore < cutoff:
                    continue
                all_domains[domain[0]] = domain[1:]     
            if len(all_domains) > 0:
                setattr(group,'%s_hit' %keyword,True)
                setattr(group,'%s_data' %keyword, ['rrefam',all_domains])
            else:
                setattr(group,'%s_hit' %keyword,False)
        else:
            setattr(group,'%s_hit' %keyword,False)
            
            
def run_hmm(groups,settings):
    settings.rrefinder_tbl_out = tbl_out = os.path.join(settings.results_folder,'RREfinder_hmm_results.tbl')
    settings.rrefinder_hmm_out = hmm_out = os.path.join(settings.results_folder,'RREfinder_hmm_results.txt')
    fasta_file_all = write_fasta_single(settings,groups)
    
    # Now run hmmer
    if not os.path.isfile(tbl_out): #Reuse old results
        hmmsearch(settings.fasta_file_all,settings.hmm_db,hmm_out,tbl_out,settings,bitscore=settings.hmm_cutoff,cut=False,cores=settings.cores)
    
    # Parse the results
    results = parse_hmm_domtbl_hmmsearch(tbl_out)
    # Interpret the results and assign RRE hits
    find_RRE_hits_hmm(groups,results,settings.hmm_cutoff,settings.hmm_minlen)
    
    
def hmmsearch(infile,database,outfile,domtblout,settings,evalue=False,cut=False,bitscore=False,cores=1):
    commands = ['hmmsearch','--cpu',str(cores),'-o',outfile,'--domtblout',domtblout]
    if evalue:
        commands.extend(['-E',str(evalue)])
    elif cut:
        commands.extend(['--%s' %cut])
    elif bitscore:
        commands.extend(['-T',str(bitscore)])
    commands.extend([database,infile])
    settings.logger.log(' '.join(commands),1)
    call(commands)
    
def write_results_summary(all_groups,outfile,mode,settings,resubmit=False,hmm=False,regulators=False):
    
    def write_group_res(group,data_attr,handle,gene=None):
        hittype,data = getattr(group,data_attr)
        text = ''
        max_reg_found = 0
        if hittype == 'hhpred':
            for hit in data:
                res = data[hit]
                if gene:
                    # Group represents a group of genes
                    # Obsolete but left here for the purpose of old parsers
                    out = [group.name,gene,hit] + [str(i) for i in res[0:6]]
                else:
                    # Group actually represents a single gene
                    out = ['N\\A',group.org_name,hit] + [str(i) for i in res[0:6]]
                out.extend( res[6].split('-') )
                if regulators and len(res) > 8:
                    # Regulator overlap found
                    nr_reg_found = len(res[8])
                    if nr_reg_found > max_reg_found:
                        max_reg_found = nr_reg_found
                    out.append(str(nr_reg_found))
                    for reg in res[8]:
                        out.extend([str(i) for i in reg])
                text += '\t'.join(out) + '\n'             
        else:
            for hit,domain_data in data.items():
                if gene:
                    out = [group.name,gene,hit,str(domain_data[2]),str(domain_data[3]),str(domain_data[0]),str(domain_data[1])]
                else:
                    out = ['N\\A',group.org_name,hit,str(domain_data[2]),str(domain_data[3]),str(domain_data[0]),str(domain_data[1])]
                
                if regulators and len(domain_data) > 4:
                    nr_reg_found = len(domain_data[4])
                    if nr_reg_found > max_reg_found:
                        max_reg_found = nr_reg_found
                    out.append(str(nr_reg_found))
                    for reg in domain_data[4]:
                        out.extend([str(i) for i in reg])
                text += '\t'.join(out) + '\n'

        return text,max_reg_found

    with open(outfile,'w') as handle:
        if not hmm:
            header = ['Group name','Gene name','Hit name','Probability','E-value','P-value','Score','SS','Columns','RRE start', 'RRE end']
        else:
            header = ['Group name','Gene name','Domain name','E-value','Bitscore','Start','End']
        if regulators:
            header.extend(['Regulators overlapping'])
            
        table_text = ''
        max_reg_found = 0
        if mode == 'rrefinder':
            if resubmit:
                hit_attr = 'RRE_resubmit_hit'
                data_attr = 'RRE_resubmit_data'
            else:
                hit_attr = 'RRE_hit'
                data_attr = 'RRE_data'
        elif mode == 'rrefam':
            hit_attr = 'RREfam_hit'
            data_attr = 'RREfam_data'
        for group in all_groups:
            if hasattr(group,hit_attr) and getattr(group,hit_attr):
                text,nr_reg_found = write_group_res(group,data_attr,handle)
                table_text += text
                if nr_reg_found > max_reg_found:
                    max_reg_found = nr_reg_found
        settings.logger.log('Max regs found: %i' %max_reg_found,2)
        for i in range(max_reg_found):
            header.extend(['Regulator name','Regulator start','Regulator end','Regulator e-value','Fraction of RRE overlapped'])   
        handle.write('\t'.join(header) + '\n')
        handle.write(table_text)
        

def pipeline_operator(all_groups,settings,worker_function,time_sleep=1):
    settings.logger.log('Splitting work over %i processes' %settings.cores,1)
    work_queue = Queue()
    results_queue = Queue()
    
    def put_jobs(groups,queue,nr):
        for group in groups[0:nr]:
            queue.put(group)
        return groups[nr:]
    
    all_groups = put_jobs(all_groups,work_queue,5*settings.cores)
    settings.logger.log('Creating workers',2)
    workers = []
    data_worker = [settings,work_queue,results_queue]
    for i in range(settings.cores):
        worker = Process(target=worker_function,args=data_worker)
        workers.append(worker)
    results = []
    for worker in workers:
        worker.start()
    
    while any([w.is_alive() for w in workers]):
#        settings.logger.log('%i workers still alive' %(len([w.is_alive() for w in workers])),2)
#        settings.logger.log('Work queue: %i; all_groups: %i' %(work_queue.qsize(),len(all_groups)),2)
        while not results_queue.empty():
            settings.logger.log('Found %i results in queue' %results_queue.qsize(),2)
            res = results_queue.get()
            results.append(res)
        if work_queue.qsize() < settings.cores:
            if len(all_groups) > 0:
                all_groups = put_jobs(all_groups,work_queue,5*settings.cores)
            else:
                for _ in range(settings.cores):
                    work_queue.put(False)
        time.sleep(time_sleep)
    while not results_queue.empty():
        res = results_queue.get()
        results.append(res)
    settings.logger.log('Joining workers',2)
    for worker in workers:
        worker.join()
    return results,workers
    
def pipeline_worker(settings,queue,done_queue):
    # Create a personal logger file
    logfile = os.path.join(settings.log_folder,'log_basic_%s.txt' %(os.getpid()))
    logger = Log(logfile,settings.verbosity)
    logger.log('Starting process id (pipeline_worker): %s'  %os.getpid(),2)
    settings_copy = settings.new()
    settings_copy.logger = logger
    RRE_targets = parse_fasta(settings.rre_fasta_path).keys()
    expand_db_path = settings.expand_database_path
    db_path = settings.rre_database_path
    
    while True:
        try:
            group = queue.get()
        except:
            time.sleep(10)
            continue
        if group == False:
            break
        logger.log('Process id %s starting work on group %s' %(os.getpid(),group.name),2)
        # Write out fasta files for each gene
        if not os.path.isfile(group.fasta_file):
            with open(group.fasta_file,'w') as handle:
                handle.write(group.fasta)
        # If the alignment needs to be expanded, do so here
        if settings_copy.rrefinder_primary_mode == 'hhpred':
            logger.log('Process id %s expanding alignment' %(os.getpid()),2)
            infile = group.fasta_file
            if settings_copy.resubmit: 
                nr_iter = settings_copy.resubmit_initial_hhblits_iter
            else:
                nr_iter = 3
            if not os.path.isfile(group.exp_alignment_file) or settings_copy.overwrite_hhblits:
                a3m_hhblits(infile,group.exp_alignment_file,expand_db_path,settings_copy,1,nr_iter)
            if settings_copy.addss == 1 or settings_copy.addss == 3:
                add_ss(group,settings_copy)
        # Run hhblits
        if settings_copy.rrefinder_primary_mode == 'hhpred':
            infile = group.exp_alignment_file
        else:
            infile = group.fasta_file
        outfile = group.results_file
        if not os.path.isfile(outfile) or settings_copy.overwrite_hhblits:
            logger.log('Process id %s running hhsearch' %(os.getpid()),2)
            hhsearch(settings_copy,infile,outfile,db_path,1)
        # Parse the results
        parse_hhpred_res(group,RRE_targets,settings_copy,resubmit=False)
        if group.RRE_hit and settings_copy.resubmit:
            resubmit_group(group,RRE_targets,settings_copy,1)
        
        logger.log('Process id %s depositing results' %os.getpid(),2)
        done_queue.put(group)
    logger.log('Process %s exiting' %os.getpid(),2)

def pipeline_resubmit_worker(settings,queue,done_queue):
    logfile = os.path.join(settings.log_folder,'log_resubmit_%s.txt' %(os.getpid()))
    logger = Log(logfile,settings.verbosity)
    settings_copy = settings.new()
    settings_copy.logger = logger
    logger.log('Starting process id (pipeline_resubmit_worker): %s'  %os.getpid(),2)
    RRE_targets = parse_fasta(settings.rre_fasta_path).keys()
    db_path = settings.rre_database_path
    while True:
        try:
            group = queue.get()
        except:
            time.sleep(10)
            continue
        if group == False:
            break
        logger.log('Process id %s starting work on group %s' %(os.getpid(),group.name),2)
        resubmit_group(group,RRE_targets,settings_copy,1)
        logger.log('Process id %s finished resubmitting' %os.getpid(),2)
        done_queue.put(group)
    logger.log('Process %s exiting' %os.getpid(),2)

def count_alignment(group,resubmit=False):
    if resubmit:
        infile = group.RRE_expalign_file
    else:
        infile = group.exp_alignment_file
    # Two lines per sequence
    c = 0
    with open(infile) as handle:
        for line in handle:
            c += 1
    return(int(c/2))
        

def rrefinder_main(settings,RRE_targets,all_groups):
    # RREfinder main function
    
    # For hhpred, it is more efficient to split up all the sequences individually, which is done here. Each thread works on a single sequence 
    # at a time and finishes it completely
    # For hmm, it is less efficient to split up all the jobs individually, since hmmsearch simply takes a fasta file containing all sequences
    if settings.rrefinder_primary_mode == 'hmm':
        run_hmm(all_groups,settings)
        if settings.resubmit:
            if int(settings.cores) < len([i for i in all_groups if i.RRE_hit]) and int(settings.cores) > 1:
                # Resubmit with pipeline operator if multiple cores are used and the amount of cores is smaller than the amount of jobs
                pos_groups,_ = pipeline_operator([i for i in all_groups if i.RRE_hit],settings,pipeline_resubmit_worker)
                all_groups = [i for i in all_groups if not i.RRE_hit] + pos_groups
            else:
                resubmit_all(all_groups,RRE_targets,settings)
            
    elif settings.rrefinder_primary_mode == 'hhpred':
        if int(settings.cores) < len(all_groups) and int(settings.cores) > 1:
            # Resubmit with pipeline operator if multiple cores are used and the amount of cores is smaller than the amount of jobs
            all_groups,_ = pipeline_operator(all_groups,settings,pipeline_worker)
        else:
            # Run the jobs one step at a time
            # Write out fasta files for each gene
            write_fasta(all_groups)
            # For gene groups, align and write a3m files (OBSOLETE)
            all_muscle(all_groups,settings)
            # If the alignment needs to be expanded, do so here
            if settings.rrefinder_primary_mode == 'hhpred':
                expand_alignment(all_groups,settings)
                # Add secondary structure
                if settings.addss == 1 or settings.addss == 3:
                    add_all_ss(all_groups,settings)
            # Run hhsearch to find RRE hits
            hhsearch_all(all_groups,settings)
            # Parse the results
            parse_all_RREs(all_groups,RRE_targets,settings)
            # Resubmit if the option is given
            if settings.resubmit:
                resubmit_all(all_groups,RRE_targets,settings)
                    
    return all_groups
    
def rrefam_main(settings,all_groups):
    # Write a fasta file and run hmm
    # store the results slightly differently
    # Set files
    settings.rrefam_tbl_out = tbl_out = os.path.join(settings.results_folder,'RREfam_hmm_results.tbl')
    settings.rrefam_hmm_out = hmm_out = os.path.join(settings.results_folder,'RREfam_hmm_results.txt')
    
    # Fasta
    write_fasta_single(settings,all_groups)
    # HMMER
    if not os.path.isfile(tbl_out): #Reuse old results
        hmmsearch(settings.fasta_file_all,settings.rrefam_database,hmm_out,tbl_out,settings,cores=settings.cores,bitscore=settings.rrefam_cutoff)
    # Parse
    results = parse_hmm_domtbl_hmmsearch(tbl_out)
    # Integrate into groups
    find_RREfam_hits(all_groups,results,settings.rrefam_cutoff,min_len=settings.rrefam_minlen,keyword='RREfam')

def scan_regulators(settings,all_groups):
    # Get the fasta file of all relevant hits
    fasta_file_hits = os.path.join(settings.fasta_folder,'fasta_RRE_hits.fasta')
    hmm_file_out = os.path.join(settings.results_folder,'RRE_hits_hmmsearch_regulator.txt')
    hmm_file_tbl = os.path.join(settings.results_folder,'RRE_hits_hmmsearch_regulator.tbl')
    hits = []
    for group in all_groups:
        if settings.mode == 'both' or settings.mode == 'rrefinder':
            if settings.resubmit:
                if group.RRE_hit and group.RRE_resubmit_hit:
                    hits.append(group)
                    continue
            else:
                if group.RRE_hit:
                    hits.append(group)
                    continue
        if settings.mode == 'both' or settings.mode == 'rrefam':
            if group.RREfam_hit:
                hits.append(group)
    
    if len(hits) == 0:
        settings.logger.log('No RRE hits found - not scanning for regulators',1)            
        return
    with open(fasta_file_hits,'w') as handle:
        for group in hits:
            handle.write(group.fasta)
    
    # Now run hmmer
    if not os.path.isfile(hmm_file_tbl): #Reuse old results
        hmmsearch(fasta_file_hits,settings.regulator_database,hmm_file_out,hmm_file_tbl,settings,cores=settings.cores,cut='cut_tc')
    
    # Parse the results
    results = parse_hmm_domtbl_hmmsearch(hmm_file_tbl)
    # The RRE locations are necessary to determine the overlap
    for group in hits:
        determine_RRE_locations(group,settings,settings.mode,resubmit=settings.resubmit)
    # Mark hits that are overlapping
    determine_regulator_overlap(results,hits,settings)


def determine_regulator_overlap(hmm_results,all_groups,settings):
    # Regulator overlap for RREfinder
    # Twice for RREfinder if resubmit was given
    # Once for RREfam
    for group in all_groups:
        if group.name in hmm_results:
            domains_found = hmm_results[group.name]
            # Determine the overlap with the RRE for each domain
            # Append to hit data
            # Do so for each of the analysis modes
            for scantype,locations in group.RRE_locations.items():
                for hit in locations:  
                    start_hit,end_hit = locations[hit]
                    length_hit = end_hit - start_hit
                    
                    overlaps_to_append = []
                    for domain in domains_found:
                        start_hmm,end_hmm = domain[1],domain[2]
                        if start_hmm <= start_hit and end_hmm >= end_hit:
                            fraction_overlap = 1.0
                        else:
                            if start_hmm <= start_hit and end_hmm < end_hit:
                                overlap = end_hmm - start_hit 
                            elif start_hmm > start_hit and end_hmm >= end_hit:
                                overlap = end_hit - start_hmm
                            elif start_hmm > start_hit and end_hmm < end_hit:
                                overlap = end_hmm - start_hmm
                            fraction_overlap = float(overlap) / length_hit
                        if fraction_overlap >= settings.min_reg_overlap:
                            # Overlap
                            overlap_data = domain + [round(fraction_overlap,3)]
                            overlaps_to_append.append(overlap_data)
                    overlaps_to_append.sort(key=lambda x: (x[-1],float(x[-2])))
                    
                    if scantype == 'RREfinder':
                        hittype,RRE_data = group.RRE_data
                        
                    elif scantype == 'RREfinder_resubmit':
                        hittype,RRE_data = group.RRE_resubmit_data
                        
                    elif scantype == 'RREfam':
                        hittype,RRE_data = group.RREfam_data
                    # Display three regulators max
                    RRE_data[hit].append(overlaps_to_append[0:3])
           
                        
def set_folders(settings):
    if not os.path.isdir(settings.outputfolder):
        os.mkdir(settings.outputfolder)
    data_folder = os.path.join(settings.outputfolder,settings.project_name)
    log_folder = os.path.join(data_folder,'subprocess_logs')
    logfile = os.path.join(data_folder,'log.txt')
    if os.path.isdir(data_folder):
        print('Warning! Output folder with name %s already found - results may be overwritten' %(settings.project_name))
    fasta_folder = os.path.join(data_folder,'fastas')
    results_folder = os.path.join(data_folder,'results')
    settings.setattrs(fasta_folder=fasta_folder,results_folder=results_folder,data_folder=data_folder,logfile=logfile,log_folder=log_folder)
    
def make_folders(settings):
    for folder in settings.data_folder,settings.fasta_folder,settings.results_folder,settings.log_folder:
        if not os.path.isdir(folder):
            os.mkdir(folder)

def parse_infiles(settings):
    if os.path.isfile(settings.infile):
        # Singular file
        infile = settings.infile
        if not ((settings.intype == 'fasta' and any([infile.endswith(ext) for ext in ['.fas','.fasta','.faa']])) or \
                (settings.intype == 'genbank' and any([infile.endswith(ext) for ext in ['.gbk','.gbff']]))):
            settings.logger.log('File of invalid type. Please specify the file type with -t or --intype',0)
            return {}
        settings.logger.log('Reading in file %s' %infile,1)
    elif os.path.isdir(settings.infile):
        # Get all the relevant files
        files = os.listdir(settings.infile)
        if settings.intype == 'fasta':
            exts = ['.fas','.fasta','.faa']
        elif settings.intype == 'genbank':
            exts = ['.gbk','.gbff']
        else:
            settings.logger.log('Non-legal file type given. Please choose from genbank or fasta',0)
            return {}
        infile = [os.path.join(settings.infile,f) for f in files if any([f.endswith(ext) for ext in exts]) and os.path.isfile(os.path.join(settings.infile,f))]
        if len(infile) == 0:
            settings.logger.log('No %s files found in folder %s' %(settings.intype,settings.infile),0)
            return {}
        else:
            settings.logger.log('%i %s files found in folder %s' %(len(infile),settings.intype,settings.infile),1)
    else:
        settings.logger.log('No valid file or directory given',0)
        return {}
    
    if settings.intype == 'fasta':
        seq_dict = parse_fasta(infile)
    elif settings.intype == 'genbank':
        seq_dict = parse_genbank(infile)
    else:
        return {}
    
    return(seq_dict)
    
def get_best_hit(rre_data):
    mode,all_data = rre_data
    best_data = [0,0,0,0]
    print(mode)
    if mode == 'hmm':
        for domain,data in all_data.items():
            print(domain,data,best_data)
            if data[3] > best_data[3]:
                print('Using new data %s' %(data))
                best_data = data
                best_domain = domain
        best_score = best_data[3]
        best_start = best_data[0]
        best_end = best_data[1]
    elif mode == 'hhpred':
        for hit,data in all_data.items():
            if data[0] > best_data[0]:
                best_data = data
                best_domain = hit
        best_score = best_data[0]
        best_start,best_end = [int(i) for i in best_data[6].split('-')]
    return best_domain,best_data,best_score, best_start, best_end
    
def add_results(all_groups,lanthi_results,resubmit=True):
    all_results = {}
    for group in all_groups:
        gene_results = []
        # Precision mode results
        if hasattr(group,'RREfam_hit') and group.RREfam_hit:
            _,all_data = group.RREfam_data
            for domain,data in all_data.items():
                rre_result = RREResult('precision',start=data[0],end=data[1],score=data[3],model=domain)
                gene_results.append(rre_result)
        # Exploratory mode results
        # First check if it was run with resubmit
        if resubmit and hasattr(group,'RRE_resubmit_hit') and group.RRE_resubmit_hit:
            # Get the data from the first filter and use the best one
            best_domain, best_data, best_score, _, _ = get_best_hit(group.RRE_data)
            # Also use only one hit from the HHPred results (different hits are not important)
            best_hit_resubmit,best_data_resubmit, best_score_resubmit, start, end = get_best_hit(group.RRE_resubmit_data)
            start,end = [int(i) for i in best_data_resubmit[6].split('-')]
            rre_result = RREResult('exploratory',resubmit=True,start=start,end=end,score1=best_score,score2=best_score_resubmit)
            gene_results.append(rre_result)
        # Otherwise just a simple run without resubmit 
        elif not resubmit and hasattr(group,'RRE_hit') and group.RRE_hit:
            best_domain, best_data, best_score, start, end = get_best_hit(group.RRE_data)
            rre_result = RREResult('exploratory',resubmit=False,start=start,end=end,score=best_score)
            gene_results.append(rre_result)
        if gene_results != []:
            all_results[group.org_name] = gene_results
    lanthi_results.RRE_by_locus.update(all_results)      
      
def main(cluster, lanthi_results, name, options):
    if options.rrefinder == 'none':
        return
    configpath = 'RREfinder_config.ini'
    settings = parse_settings(os.path.join(os.path.dirname(__file__),configpath),name,options)
    if settings.rrefinder_primary_mode == 'hhpred' and not settings.expand_database_path:
        print('Using HHpred as initial mode for RREfinder requires an HHblits database. Please set the path in the config file')
        exit()
    t0 = time.time()

    # Set folders and other files
    set_folders(settings)
    
    # Make the logfile
    logger = Log(settings.logfile,settings.verbosity)
    settings.logger = logger

    # Convert the cluster to the objects used in this script
    all_groups,skipped_genes = convert_cluster(cluster,settings)
    if len(all_groups) == 0:
        # No genes to be analyzed
        return

    # Make some folders
    make_folders(settings)
    # Get the names of the targets that are considered RRE hits
    RRE_targets = parse_fasta(settings.rre_fasta_path).keys()
        
    settings.logger.log('Analyzing %i queries' %len(all_groups),1)
    settings.logger.log('Skipped %i queries (too long)' %(len(skipped_genes)),2)
    if settings.mode == 'rrefinder' or settings.mode == 'both':
        all_groups = rrefinder_main(settings,RRE_targets,all_groups)
    if settings.mode == 'rrefam' or settings.mode == 'both':
        rrefam_main(settings,all_groups)
    if settings.regulator_filter:
        scan_regulators(settings,all_groups)
    
    # Summary files
    if settings.mode == 'rrefinder' or settings.mode == 'both':
        outfile = os.path.join(settings.data_folder,'%s_rrefinder_exploratory_results.txt' %settings.project_name)
        write_results_summary(all_groups,outfile,'rrefinder',settings,hmm=(settings.rrefinder_primary_mode=='hmm'),regulators=settings.regulator_filter)
        if settings.resubmit:
            outfile_resubmit = os.path.join(settings.data_folder,'%s_rrefinder_exploratory_resubmit_results.txt' %(settings.project_name))
            write_results_summary(all_groups,outfile_resubmit,'rrefinder',settings,resubmit=True,regulators=settings.regulator_filter)
    if settings.mode == 'rrefam' or settings.mode == 'both':
        outfile_rrefam = os.path.join(settings.data_folder,'%s_rrefinder_precision_results.txt' %settings.project_name)
        write_results_summary(all_groups,outfile_rrefam,'rrefam',settings,resubmit=False,hmm=True)
    
    # Add the results to the LanthiResults object
    add_results(all_groups, lanthi_results)
    
    t1 = time.time()
    settings.logger.log('Finished. Total time: %.2f seconds (on %i cores)' %((t1-t0),settings.cores),1)
    if settings.mode == 'rrefinder' or settings.mode == 'both':
        if settings.resubmit:
            settings.logger.log('RREfinder exploratory mode hits found: %i out of %i' %(len([i for i in all_groups if i.RRE_hit and i.RRE_resubmit_hit]),len(all_groups)),1)
        else:
            settings.logger.log('RREfinder exploratory mode hits found: %i out of %i' %(len([i for i in all_groups if i.RRE_hit]),len(all_groups)),1)
    if settings.mode == 'rrefam' or settings.mode == 'both':
        settings.logger.log('RREfinder precision mode hits found: %i out of %i' %(len([i for i in all_groups if i.RREfam_hit]),len(all_groups)),1)
    
    return 

class Container():
    # Simple container used to store info on various things
    def __init__(self):
        pass
        
    def setattrs(self,**kwargs):
        for key,value in kwargs.items():
            setattr(self,key,value)
            
    def new(self):
        c = Container()
        for item,value in self.__dict__.items():
            setattr(c,item,value)
        return c
            
            
class GeneObject(Container):
    pass  
        
class SettingsObject(Container):
    pass
            
class Log():
    def __init__(self,logfile,verbosity,remove_first=True):
        self.logfile = logfile
        self.verbosity = verbosity
        if remove_first and os.path.isfile(logfile):
            os.remove(logfile)
    
    def log(self,text,verbosity_req,write=True):
        if self.verbosity >= verbosity_req:
            print(text)
            if write:
                with open(self.logfile,'a') as handle:
                    handle.write(text + '\n')

class RREResult(module_results.ModuleResults):
    def __init__(self,mode,**kwargs):
        self.mode = mode
        for key,value in kwargs.items():
            setattr(self,key,value)
    
def parse_config(configpath):
    config = configparser.ConfigParser()
    config.read(configpath)
    settings = SettingsObject()
    paths = ['outputfolder',
             'regulator_database',
             'rre_database_path',
             'rre_fasta_path',
             'expand_database_path',
             'resubmit_database',
             'hmm_db',
             'rrefam_database']
    
    def get_value(value):
        try:
            value = int(value)
        except ValueError:
            try:
                value = float(value)
            except ValueError:
                try:
                    value = config.getboolean(section,item)
                except ValueError:
                    if ',' in value:
                        values = value.split(',')
                        new = []
                        for v in values:
                            new.append(get_value(v))
                        value = new
        return(value)
        
    for section in config.sections():
        items = config.items(section)
        for item,value in items:
            value = get_value(value)
            # All the paths should be set relative to the script
            if item in paths:
                value = os.path.join(os.path.dirname(__file__),value)
            setattr(settings,item,value)
    return settings

def parse_settings(configpath,name,options):
    settings = parse_config(configpath)
    if options.rrefinder == 'precision':
        settings.mode = 'rrefam'
    elif options.rrefinder == 'exploratory':
        settings.mode = 'rrefinder'
    elif options.rrefinder == 'both':
        settings.mode = 'both'
    settings.project_name = name
    return settings

