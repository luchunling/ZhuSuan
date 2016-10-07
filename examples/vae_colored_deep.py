#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import absolute_import
from __future__ import print_function
from __future__ import division
import sys
import os
import time
from collections import namedtuple
import tensorflow as tf
import prettytensor as pt
from six.moves import range, reduce, map, zip
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from zhusuan.distributions import norm, bernoulli, logistic
    from zhusuan.layers import *
    from zhusuan.variational import advi
    from zhusuan.evaluation import is_loglikelihood
except:
    raise ImportError()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import dataset
    from deconv import deconv2d
    from adamax import AdamaxOptimizer
except:
    raise ImportError()


def split(x, split_dim, split_sizes):
    n = len(list(x.get_shape()))
    dim_size = np.sum(split_sizes)
    assert int(x.get_shape()[split_dim]) == dim_size
    ids = np.cumsum([0] + split_sizes)
    ids[-1] = -1
    begin_ids = ids[:-1]

    ret = []
    for i in range(len(split_sizes)):
        cur_begin = np.zeros([n], dtype=np.int32)
        cur_begin[split_dim] = begin_ids[i]
        cur_end = np.zeros([n], dtype=np.int32) - 1
        cur_end[split_dim] = split_sizes[i]
        ret += [tf.slice(x, cur_begin, cur_end)]
    return ret


def resize_nearest_neighbor(x, scale):
    input_shape = tf.shape(x)
    size = [tf.cast(tf.cast(input_shape[1], tf.float32) * scale, tf.int32),
            tf.cast(tf.cast(input_shape[2], tf.float32) * scale, tf.int32)]
    x = tf.image.resize_nearest_neighbor(x, size)
    return x


def discretized_logistic(mean, logscale, binsize=1 / 256.0, sample=None):
    scale = tf.exp(logscale)
    sample = (tf.floor(sample / binsize) * binsize - mean) / scale
    logp = tf.log(tf.sigmoid(sample + binsize / scale) - tf.sigmoid(sample) + 1e-7)
    return tf.reduce_sum(logp, 2)


class M1:
    """
    The deep generative model used in variational autoencoder (VAE).

    :param n_z: Int. The dimension of latent variables (z).
    :param n_x: Int. The dimension of observed variables (x).
    """
    def __init__(self, n_x, n_z, n_h1, n_h2, groups):
        self.n_x = n_x
        self.down_convs = {}
        self.n_h1 = n_h1
        self.n_h2 = n_h2
        self.n_z = n_z
        self.groups = groups
        with pt.defaults_scope(activation_fn=None):
            for group_i, group in reversed(list(enumerate(
                    self.groups))):
                for block_i in reversed(range(group.num_blocks)):
                    name = 'group_%d/block_%d' % (group_i, block_i)
                    stride = 1
                    if group_i > 0 and block_i == 0:
                        stride = 2
                    self.down_convs[name+'_down_conv1'] = (
                        pt.template('h1').
                        reshape([-1, group.map_size,
                                 group.map_size, self.n_h1]).
                        apply(tf.nn.elu).
                        deconv2d(3, self.n_h2 + 2 * self.n_z,
                                 name=name+'_down_conv1',
                                 activation_fn=None))

                    self.down_convs[name+'_down_conv2'] = (
                        pt.template('h2_plus_z').
                        reshape([-1, group.map_size, group.map_size,
                                 self.n_h2 + self.n_z]).
                        apply(tf.nn.elu).
                        deconv2d(3, self.n_h1, stride=stride,
                                 name=name+'_down_conv2',
                                 activation_fn=None))
            self.l_x_z = (pt.template('h').
                          reshape([-1, self.groups[0].map_size,
                                   self.groups[0].map_size, self.n_h1]).
                          apply(tf.nn.elu).
                          deconv2d(5, self.n_h1, stride=2,
                                   activation_fn=tf.nn.elu).
                          deconv2d(3, 3, activation_fn=None))
        self.x_logsd = tf.get_variable('x_logsd',  (),
                                       initializer=tf.zeros_initializer)
        self.h_top = tf.get_variable('h_top', [self.n_h1],
                                     initializer=tf.zeros_initializer)

    def log_prob(self, latent, observed, given):
        """
        The log joint probability function.

        :param latent: A dictionary of pairs: (string, Tensor). Each of the
            Tensor has shape (batch_size, n_samples, n_latent).
        :param observed: A dictionary of pairs: (string, Tensor). Each of the
            Tensor has shape (batch_size, n_observed).

        :return: A Tensor of shape (batch_size, n_samples). The joint log
            likelihoods.
        """
        x = observed['x']
        n_samples = tf.shape(latent['z_group_0/block_0'])[1]
        batch_size = tf.shape(latent['z_group_0/block_0'])[0]
        h_top = tf.reshape(self.h_top, [1, 1, 1, -1])
        h_top = tf.tile(h_top, [batch_size*n_samples,
                                self.groups[-1].map_size,
                                self.groups[-1].map_size, 1])
        h = h_top
        log_pzs = []
        for group_i, group in reversed(list(enumerate(self.groups))):
            for block_i in reversed(range(group.num_blocks)):
                name = 'group_%d/block_%d' % (group_i, block_i)
                print(name)
                input_h = h
                z = latent['z_'+name]
                h_2 = self.down_convs[name+'_down_conv1'].construct(
                    h1=h).tensor
                h_det, pz_mean, pz_logsd = split(h_2, -1, [self.n_h2,
                                                           self.n_z, self.n_z])
                pz_mean = tf.reshape(pz_mean, [-1, n_samples,
                                               group.map_size,
                                               group.map_size, self.n_z])
                pz_logsd = tf.reshape(pz_logsd, [-1, n_samples,
                                                 group.map_size,
                                                 group.map_size, self.n_z])
                log_pzs.append(tf.reduce_sum(norm.logpdf(z, pz_mean, tf.exp(
                    pz_logsd)), [2, 3, 4]))
                z = tf.reshape(z, [-1, group.map_size, group.map_size, self.n_z])
                h = tf.concat(3, [h_det, z])
                if group_i > 0 and block_i == 0:
                    input_h = resize_nearest_neighbor(input_h, 2)
                h = self.down_convs[name+'_down_conv2'].construct(
                    h2_plus_z=h).tensor
                h = input_h + 0.1 * h
        x_mean = self.l_x_z.construct(h=h).reshape(
            [-1, n_samples, self.n_x]).tensor
        x_mean = tf.clip_by_value(x_mean * 0.1, -0.5 + 1 / 512., 0.5 - 1 / 512.)

        x = tf.expand_dims(x, 1)
        log_px_z1 = discretized_logistic(x_mean, self.x_logsd, sample=x)

        return log_px_z1 + tf.add_n(log_pzs)


def q_net(n_x, n_xl, n_samples, n_z, n_h1, n_h2, groups):
    """
    Build the recognition network (Q-net) used as variational posterior.

    :param n_x: Int. The dimension of observed variables (x).
    :param n_samples: A Int or a Tensor of type int. Number of samples of
        latent variables.

    :return: All :class:`Layer` instances needed.
    """
    with pt.defaults_scope(activation_fn=None):
        lx = InputLayer((None, n_x))
        # l_h shape: (batch_size, 16, 16, 64)
        l_h = PrettyTensor({'x': lx},
                                  pt.template('x').
                                  reshape([-1, n_xl, n_xl, 3]).
                                  conv2d(5, n_h1, stride=2,
                                         activation_fn=None))
        lzs = {}
        for group_i, group in enumerate(groups):
            for block_i in range(group.num_blocks):
                name = 'group_%d/block_%d' % (group_i, block_i)
                # print(name)
                stride = 1
                if group_i > 0 and block_i == 0:
                    stride = 2
                up_conv1_h = (
                    pt.template('h1').
                    apply(tf.nn.elu).
                    conv2d(3, n_h2, stride=stride, name=name+'_up_conv1_h',
                           activation_fn=None))
                up_conv1_z_mean = (
                    pt.template('h1').
                    apply(tf.nn.elu).
                    conv2d(3, n_z, stride=stride, name=name+'_up_conv1_z_mean',
                           activation_fn=None).
                    reshape([-1, 1, group.map_size, group.map_size, n_z])
                )
                up_conv1_z_logvar = (
                    pt.template('h1').
                    apply(tf.nn.elu).
                    conv2d(3, n_z, stride=stride, activation_fn=None,
                           name=name+'_up_conv1_z_logvar').
                    reshape([-1, 1, group.map_size, group.map_size, n_z]))
                up_conv2_h = (
                    pt.template('h2').
                    apply(tf.nn.elu).
                    conv2d(3, n_h1, name=name+'_up_conv2_h',
                           activation_fn=None))

                l_h2 = PrettyTensor({'h1': l_h}, up_conv1_h)
                l_z_mean = PrettyTensor({'h1': l_h}, up_conv1_z_mean)
                l_z_logvar = PrettyTensor({'h1': l_h}, up_conv1_z_logvar)
                lzs['z_'+name] = Normal([l_z_mean, l_z_logvar], n_samples)
                l_h2 = PrettyTensor({'h2': l_h2}, up_conv2_h)
                if group_i > 0 and block_i == 0:
                    l_h = PrettyTensor({'h1': l_h}, (
                        pt.template('h1').
                        apply(resize_nearest_neighbor, 0.5)))
                l_h = PrettyTensor({'h1': l_h, 'h2': l_h2}, (
                    pt.template('h1').
                    apply(tf.add, pt.template('h2').apply(tf.mul, 0.1))))

    return lx, lzs


if __name__ == "__main__":
    tf.set_random_seed(1237)

    # Load CIFAR
    data_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             'data', 'cifar10', 'cifar-10-python.tar.gz')
    np.random.seed(1234)
    x_train, t_train, x_test, t_test = \
        dataset.load_cifar10(data_path, normalize=True, one_hot=True)
    print(x_train.max(), x_train.min())
    print(x_test.max(), x_test.min())
    _, n_xl, _, n_channels = x_train.shape
    n_x = n_xl * n_xl * n_channels
    x_train = x_train.reshape((-1, n_x))
    x_test = x_test.reshape((-1, n_x))
    n_y = t_train.shape[1]

    # Define training/evaluation parameters
    lb_samples = 10
    ll_samples = 100
    epoches = 3000
    batch_size = 16
    test_batch_size = 100
    iters = x_train.shape[0] // batch_size
    test_iters = x_test.shape[0] // test_batch_size
    test_freq = 10
    learning_rate = 0.001
    anneal_lr_freq = 200
    anneal_lr_rate = 0.75

    n_h1 = 64
    n_h2 = 64
    n_z = 32
    bottle_neck_group = namedtuple(
        'bottle_neck_group',
        ['num_blocks', 'num_filters', 'map_size'])
    groups = [
        bottle_neck_group(2, 64, 16),
        bottle_neck_group(2, 64, 8),
        bottle_neck_group(2, 64, 4)
    ]

    # settings
    flags = tf.flags
    flags.DEFINE_string("model_file", "",
                        "restoring model file")
    flags.DEFINE_string("save_dir", os.environ['MODEL_RESULT_PATH_AND_PREFIX'],
                        'path and prefix to save params')
    flags.DEFINE_integer("save_freq", 50, 'save frequency of param file')

    FLAGS = flags.FLAGS

    def build_model(phase, reuse=False):
        with pt.defaults_scope(phase=phase):
            with tf.variable_scope("model", reuse=reuse) as scope:
                model = M1(n_x, n_z, n_h1, n_h2, groups)
            with tf.variable_scope("variational", reuse=reuse) as scope:
                lx, lzs = q_net(n_x, n_xl, n_samples, n_z, n_h1, n_h2, groups)
        return model, lx, lzs

    # Build the training computation graph
    learning_rate_ph = tf.placeholder(tf.float32, shape=[])
    x = tf.placeholder(tf.float32, shape=(None, n_x))
    n_samples = tf.placeholder(tf.int32, shape=())
    optimizer = tf.train.AdamOptimizer(learning_rate_ph)
    # optimizer = AdamaxOptimizer(learning_rate_ph)
    model, lx, lzs = build_model(pt.Phase.train)
    lz_key, lz_list = map(list, zip(*six.iteritems(lzs)))
    z_outputs = get_output(lz_list, x)
    latent = dict(zip(lz_key, z_outputs))
    lower_bound = tf.reduce_mean(advi(
        model, {'x': x}, latent, reduction_indices=1))
    bits_per_dim = -lower_bound / n_x * 1. / np.log(2.)
    grads_and_vars = optimizer.compute_gradients(bits_per_dim)
    # grads = [gv[0] for gv in grads_and_vars]
    # params = [gv[1] for gv in grads_and_vars]
    # grads, _ = tf.clip_by_global_norm(grads, 5)
    # grads_and_vars = list(zip(grads, params))

    def l2_norm(x):
        return tf.sqrt(tf.reduce_sum(tf.square(x)))

    update_ratio = learning_rate_ph * tf.reduce_mean(tf.pack(list(
        (l2_norm(k) / (l2_norm(v) + 1e-8)) for k, v in grads_and_vars
        if k is not None)))
    infer = optimizer.apply_gradients(grads_and_vars)

    # Build the evaluation computation graph
    eval_model, eval_lx, eval_lzs = build_model(
        pt.Phase.test, reuse=True)
    eval_lz_key, eval_lz_list = map(list, zip(*six.iteritems(eval_lzs)))
    z_outputs = get_output(eval_lz_list, x)
    eval_latent = dict(zip(eval_lz_key, z_outputs))
    eval_lower_bound = tf.reduce_mean(advi(
        eval_model, {'x': x}, latent, reduction_indices=1))
    eval_bits_per_dim = -eval_lower_bound / n_x * 1. / np.log(2.)
    eval_log_likelihood = tf.reduce_mean(is_loglikelihood(
        eval_model, {'x': x}, latent, reduction_indices=1))
    eval_bits_per_dim_ll = -eval_log_likelihood / n_x * 1. / np.log(2.)

    params = tf.trainable_variables()
    for i in params:
        print(i.name, i.get_shape())

    init = tf.initialize_all_variables()
    saver = tf.train.Saver()

    # Run the inference
    with tf.Session() as sess:
        sess.run(init)
        if FLAGS.model_file is not "":
            saver.restore(sess, FLAGS.model_file)
        for epoch in range(1, epoches + 1):
            time_epoch = -time.time()
            if epoch % anneal_lr_freq == 0:
                learning_rate *= anneal_lr_rate
            np.random.shuffle(x_train)
            lbs = []
            bitss = []
            update_ratios = []
            for t in range(iters):
                x_batch = x_train[t * batch_size:(t + 1) * batch_size]

                _, lb, bits, update_ratio_ = sess.run([infer, lower_bound,
                                               bits_per_dim, update_ratio],
                                 feed_dict={x: x_batch,
                                            learning_rate_ph: learning_rate,
                                            n_samples: lb_samples})
                update_ratios.append(update_ratio_)
                lbs.append(lb)
                bitss.append(bits)
            time_epoch += time.time()
            print('Epoch {} ({:.1f}s): Lower bound = {} bits = {}'.format(
                epoch, time_epoch, np.mean(lbs), np.mean(bitss)))
            print('update ratio = {}'.format(np.mean(update_ratios)))
            if FLAGS.save_freq is not 0 and epoch % FLAGS.save_freq is 0:
                saver.save(sess, FLAGS.save_dir+"model_{0}.ckpt".format(epoch))
            if epoch % test_freq == 0:
                time_test = -time.time()
                test_lbs = []
                test_lb_bitss = []
                test_lls = []
                test_ll_bitss = []
                for t in range(test_iters):
                    test_x_batch = x_test[
                        t * test_batch_size: (t + 1) * test_batch_size]
                    test_lb, test_lb_bits = sess.run(
                        [eval_lower_bound, eval_bits_per_dim],
                        feed_dict={x: test_x_batch,
                                   n_samples: lb_samples})
                    test_ll, test_ll_bits = sess.run(
                        [eval_log_likelihood, eval_bits_per_dim_ll],
                        feed_dict={x: test_x_batch,
                                   n_samples: ll_samples})
                    test_lbs.append(test_lb)
                    test_lb_bitss.append(test_lb_bits)
                    test_lls.append(test_ll)
                    test_ll_bitss.append(test_ll_bits)
                time_test += time.time()
                print('>>> TEST ({:.1f}s)'.format(time_test))
                print('>> Test lower bound = {}, bits = {}'.format(
                    np.mean(test_lbs), np.mean(test_lb_bitss)))
                print('>> Test log likelihood = {}, bits = {}'.format(
                    np.mean(test_lls), np.mean(test_ll_bitss)))
