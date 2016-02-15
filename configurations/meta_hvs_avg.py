from collections import namedtuple
import lasagne as nn
import data_iterators
import numpy as np
import theano.tensor as T
import utils
import nn_heart
from configuration import subconfig

caching = None
restart_from_save = None

rng = subconfig().rng
patch_size = subconfig().patch_size
train_transformation_params = subconfig().train_transformation_params
valid_transformation_params = subconfig().valid_transformation_params

batch_size = 3
nbatches_chunk = 2
chunk_size = batch_size * nbatches_chunk

train_data_iterator = data_iterators.PatientsDataGenerator(data_path='/data/dsb15_pkl/pkl_splitted/train',
                                                           batch_size=chunk_size,
                                                           transform_params=train_transformation_params,
                                                           labels_path='/data/dsb15_pkl/train.csv',
                                                           full_batch=True, random=True, infinite=True)

valid_data_iterator = data_iterators.PatientsDataGenerator(data_path='/data/dsb15_pkl/pkl_splitted/valid',
                                                           batch_size=chunk_size,
                                                           transform_params=valid_transformation_params,
                                                           labels_path='/data/dsb15_pkl/train.csv',
                                                           full_batch=False, random=False, infinite=False)

test_data_iterator = data_iterators.PatientsDataGenerator(data_path='/data/dsb15_pkl/pkl_validate',
                                                          batch_size=batch_size,
                                                          transform_params=train_transformation_params,
                                                          full_batch=False, random=False, infinite=False)

# find maximum number of slices
nslices = max(train_data_iterator.nslices,
              valid_data_iterator.nslices,
              test_data_iterator.nslices)

# set this max for every data iterator
train_data_iterator.nslices = nslices
valid_data_iterator.nslices = nslices
test_data_iterator.nslices = nslices

nchunks_per_epoch = train_data_iterator.nsamples / chunk_size
max_nchunks = nchunks_per_epoch * 100
learning_rate_schedule = {
    0: 0.0001,
    int(max_nchunks * 0.25): 0.00007,
    int(max_nchunks * 0.5): 0.00003,
    int(max_nchunks * 0.75): 0.00001,
}
validate_every = nchunks_per_epoch
save_every = nchunks_per_epoch


def build_model():
    l_in = nn.layers.InputLayer((None, nslices, 30) + patch_size)
    l_in_slice_mask = nn.layers.InputLayer((None, nslices))
    l_in_slice_location = nn.layers.InputLayer((None, nslices, 1))
    l_ins = [l_in, l_in_slice_mask, l_in_slice_location]

    l_in_rshp = nn.layers.ReshapeLayer(l_in, (-1, 30) + patch_size)  # (batch_size*nslices, 30, h,w)
    submodel = subconfig().build_model(l_in_rshp)

    # ------------------ systole
    l_sub_sys_out = submodel.l_outs[0]
    l_sub_sys_out = nn.layers.ReshapeLayer(l_sub_sys_out, (-1, nslices, [1]))


    # ------------------ diastole
    l_sub_dst_out = submodel.l_outs[1]
    l_sub_dst_out = nn.layers.ReshapeLayer(l_sub_dst_out, (-1, nslices, 1))


    l_target_mu0 = nn.layers.InputLayer((None, 1))
    l_target_mu1 = nn.layers.InputLayer((None, 1))
    l_targets = [l_target_mu0, l_target_mu1]

    l_outs = [l_cdf0, l_cdf1]
    l_top = nn.layers.MergeLayer(l_outs)

    train_params = nn.layers.get_all_params(l_top)
    submodel_params = nn.layers.get_all_params(submodel.l_top)
    train_params = [p for p in train_params if p not in submodel_params]

    return namedtuple('Model', ['l_ins', 'l_outs', 'l_targets', 'l_top', 'train_params', 'submodel'])(l_ins, l_outs,
                                                                                                      l_targets,
                                                                                                      l_top,
                                                                                                      train_params,
                                                                                                      submodel)


def build_objective(model, deterministic=False):
    p0 = nn.layers.get_output(model.l_outs[0], deterministic=deterministic)
    t0 = nn.layers.get_output(model.l_targets[0])
    t0_heaviside = nn_heart.heaviside(t0)

    crps0 = T.mean((p0 - t0_heaviside) ** 2)

    p1 = nn.layers.get_output(model.l_outs[1], deterministic=deterministic)
    t1 = nn.layers.get_output(model.l_targets[1])
    t1_heaviside = nn_heart.heaviside(t1)

    crps1 = T.mean((p1 - t1_heaviside) ** 2)

    return 0.5 * (crps0 + crps1)


def build_updates(train_loss, model, learning_rate):
    updates = nn.updates.adam(train_loss, model.train_params, learning_rate)
    return updates


def get_mean_validation_loss(batch_predictions, batch_targets):
    return [0, 0]


def get_mean_crps_loss(batch_predictions, batch_targets, batch_ids):
    nbatches = len(batch_predictions)
    npredictions = len(batch_predictions[0])

    crpss = []
    for i in xrange(npredictions):
        p, t = [], []
        for j in xrange(nbatches):
            p.append(batch_predictions[j][i])
            t.append(batch_targets[j][i])
        p, t = np.vstack(p), np.vstack(t)
        target_cdf = utils.heaviside_function(t)
        crpss.append(np.mean((p - target_cdf) ** 2))

    return crpss