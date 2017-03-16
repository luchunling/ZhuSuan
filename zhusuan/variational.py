#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import absolute_import
from __future__ import print_function
from __future__ import division

import tensorflow as tf
import six
from six.moves import zip, map
from tensorflow.python.training import moving_averages

from .utils import log_mean_exp, merge_dicts
from .evaluation import is_loglikelihood


__all__ = [
    'advi',
    'iwae',
    'rws',
    'nvil',
    'ali'
]


def advi(log_joint, observed, latent, axis=0):
    """
    Implements the automatic differentiation variational inference (ADVI)
    algorithm. This only works for continuous latent `StochasticTensor` s that
    can be reparameterized (Kingma, 2013).

    :param log_joint: A function that accepts a dictionary argument of
        (str, Tensor) pairs, which are mappings from all `StochasticTensor`
        names in the model to their observed values. The function should
        return a Tensor, representing the log joint likelihood of the model.
    :param observed: A dictionary of (str, Tensor) pairs. Mapping from names
        of observed `StochasticTensor` s to their values
    :param latent: A dictionary of (str, (Tensor, Tensor)) pairs. Mapping
        from names of latent `StochasticTensor` s to their samples and log
        probabilities.
    :param axis: The sample dimension(s) to reduce when computing the
        variational lower bound.

    :return: A Tensor. The variational lower bound.
    """
    latent_k, latent_v = map(list, zip(*six.iteritems(latent)))
    latent_outputs = dict(zip(latent_k, map(lambda x: x[0], latent_v)))
    latent_logpdfs = map(lambda x: x[1], latent_v)
    joint_obs = merge_dicts(observed, latent_outputs)
    lower_bound = log_joint(joint_obs) - sum(latent_logpdfs)
    lower_bound = tf.reduce_mean(lower_bound, axis)
    return lower_bound


def iwae(log_joint, observed, latent, axis=0):
    """
    Implements the importance weighted lower bound from (Burda, 2015).
    This only works for continuous latent `StochasticTensor` s that
    can be reparameterized (Kingma, 2013).

    :param log_joint: A function that accepts a dictionary argument of
        (str, Tensor) pairs, which are mappings from all `StochasticTensor`
        names in the model to their observed values. The function should
        return a Tensor, representing the log joint likelihood of the model.
    :param observed: A dictionary of (str, Tensor) pairs. Mapping from names
        of observed `StochasticTensor` s to their values.
    :param latent: A dictionary of (str, (Tensor, Tensor)) pairs. Mapping
        from names of latent `StochasticTensor` s to their samples and log
        probabilities.
    :param axis: The sample dimension(s) to reduce when computing the
        variational lower bound.

    :return: A Tensor. The importance weighted lower bound.
    """
    return is_loglikelihood(log_joint, observed, latent, axis)


def rws(log_joint, observed, latent, axis=0):
    """
    Implements Reweighted Wake-sleep from (Bornschein, 2015). This works for
    both continuous and discrete latent `StochasticTensor` s.

    :param log_joint: A function that accepts a dictionary argument of
        (str, Tensor) pairs, which are mappings from all `StochasticTensor`
        names in the model to their observed values. The function should
        return a Tensor, representing the log joint likelihood of the model.
    :param observed: A dictionary of (str, Tensor) pairs. Mapping from names
        of observed `StochasticTensor` s to their values.
    :param latent: A dictionary of (str, (Tensor, Tensor)) pairs. Mapping
        from names of latent `StochasticTensor` s to their samples and log
        probabilities.
    :param axis: The sample dimension(s) to reduce when computing the
        log likelihood and the cost for adapting proposals.

    :return: A Tensor. The cost to minimize given by Reweighted Wake-sleep.
    :return: A Tensor. Estimated log likelihoods.
    """
    latent_k, latent_v = map(list, zip(*six.iteritems(latent)))
    latent_outputs = dict(zip(latent_k, map(lambda x: x[0], latent_v)))
    latent_logpdfs = map(lambda x: x[1], latent_v)
    joint_obs = merge_dicts(observed, latent_outputs)
    log_joint_value = log_joint(joint_obs)
    entropy = -sum(latent_logpdfs)
    log_w = log_joint_value + entropy
    log_w_max = tf.reduce_max(log_w, axis, keep_dims=True)
    w_u = tf.exp(log_w - log_w_max)
    w_tilde = tf.stop_gradient(w_u / tf.reduce_sum(w_u, axis, keep_dims=True))
    log_likelihood = log_mean_exp(log_w, axis)
    fake_log_joint_cost = -tf.reduce_sum(w_tilde * log_joint_value, axis)
    fake_proposal_cost = tf.reduce_sum(w_tilde * entropy, axis)
    cost = fake_log_joint_cost + fake_proposal_cost
    return cost, log_likelihood


def nvil(log_joint,
         observed,
         latent,
         baseline=None,
         decay=0.8,
         variance_normalization=False,
         axis=0):
    """
    Implements the variance reduced score function estimator for gradients
    of the variational lower bound from (Mnih, 2014). This algorithm is also
    called "REINFORCE" or "baseline". This works for both continuous and
    discrete latent `StochasticTensor` s.

    :param log_joint: A function that accepts a dictionary argument of
        (str, Tensor) pairs, which are mappings from all `StochasticTensor`
        names in the model to their observed values. The function should
        return a Tensor, representing the log joint likelihood of the model.
    :param observed: A dictionary of (str, Tensor) pairs. Mapping from names
        of observed `StochasticTensor` s to their values.
    :param latent: A dictionary of (str, (Tensor, Tensor)) pairs. Mapping
        from names of latent `StochasticTensor` s to their samples and log
        probabilities.
    :param baseline: A Tensor with the same shape as returned by `log_joint`.
        A trainable estimation for the scale of the variational lower bound,
        which is typically dependent on observed values, e.g., a neural
        network with observed values as inputs.
    :param variance_normalization: Whether to use variance normalization.
    :param decay: Float. The moving average decay for variance normalization.
    :param axis: The sample dimension(s) to reduce when computing the
        variational lower bound.

    :return: A Tensor. The cost to minimize.
    :return: A Tensor. The variational lower bound.
    """
    latent_k, latent_v = map(list, zip(*six.iteritems(latent)))
    latent_outputs = dict(zip(latent_k, map(lambda x: x[0], latent_v)))
    latent_logpdfs = map(lambda x: x[1], latent_v)
    joint_obs = merge_dicts(observed, latent_outputs)
    log_joint_value = log_joint(joint_obs)
    entropy = -sum(latent_logpdfs)
    l_signal = log_joint_value + entropy
    cost = 0.

    if baseline is not None:
        baseline = tf.expand_dims(baseline, axis)
        baseline_cost = 0.5 * tf.reduce_mean(tf.square(
            tf.stop_gradient(l_signal) - baseline), axis)
        l_signal = l_signal - baseline
        cost += baseline_cost

    if variance_normalization is True:
        bc = tf.reduce_mean(l_signal)
        bv = tf.reduce_mean(tf.square(l_signal - bc))
        moving_mean = tf.get_variable('moving_mean', shape=[],
                                      initializer=tf.zeros_initializer,
                                      trainable=False)
        moving_variance = tf.get_variable('moving_variance', shape=[],
                                          initializer=tf.ones_initializer,
                                          trainable=False)

        update_mean = moving_averages.assign_moving_average(
            moving_mean, bc, decay=decay)
        update_variance = moving_averages.assign_moving_average(
            moving_variance, bv, decay=decay)
        l_signal = (l_signal - moving_mean) / tf.maximum(
            1., tf.sqrt(moving_variance))
        with tf.control_dependencies([update_mean, update_variance]):
            l_signal = tf.identity(l_signal)

    fake_log_joint_cost = -tf.reduce_mean(log_joint_value, axis)
    fake_variational_cost = tf.reduce_mean(
        tf.stop_gradient(l_signal) * entropy, axis)
    cost += fake_log_joint_cost + fake_variational_cost
    lower_bound = tf.reduce_mean(log_joint_value + entropy, axis)
    return cost, lower_bound


def ali(classifier, encoder, decoder):
    """
    Implements the Adversarial Learned Inference (ALI) algorithm.

    :param classifier: A function that accepts a Tensor. The function should
        return a Tensor, representing the unnormalized log probability that
        the input is real instead of being generated by decoder.
    :param encoder: A dict of (str, Tenosr) mapping from 'x' and 'z' to their
        corresponding value.
    :param decoder: A dict of (str, Tenosr) mapping from 'x' and 'z' to their
        corresponding value.

    :return disc_loss: A Tensor. The loss to be minimized by discriminator.
    :return gen_loss: A Tensor. The loss to be minimized by generator.
    """
    enc_class_logits = classifier(encoder['x'], encoder['z'])
    dec_class_logits = classifier(decoder['x'], decoder['z'])

    disc_loss = tf.reduce_mean(
        tf.nn.sigmoid_cross_entropy_with_logits(
            labels=tf.ones_like(enc_class_logits),
            logits=enc_class_logits)) + \
        tf.reduce_mean(
            tf.nn.sigmoid_cross_entropy_with_logits(
                labels=tf.zeros_like(dec_class_logits),
                logits=dec_class_logits))
    gen_loss = tf.reduce_mean(
        tf.nn.sigmoid_cross_entropy_with_logits(
            labels=tf.zeros_like(enc_class_logits),
            logits=enc_class_logits)) + \
        tf.reduce_mean(
            tf.nn.sigmoid_cross_entropy_with_logits(
                labels=tf.ones_like(dec_class_logits),
                logits=dec_class_logits))
    return disc_loss, gen_loss
