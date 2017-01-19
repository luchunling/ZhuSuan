#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import absolute_import
from __future__ import division

from copy import copy

import six
from six.moves import zip, map
import tensorflow as tf

from zhusuan.utils import add_name_scope


def random_momentum(mass):
    return [tf.random_normal(tf.shape(m)) * tf.sqrt(m) for m in mass]


def velocity(momentum, mass):
    return map(lambda (x, y): x / y, zip(momentum, mass))


def hamiltonian(q, p, log_posterior, mass, axis):
    # (n_particles, n)
    potential = -log_posterior(q)
    # (n_particles, n)
    kinetic = 0.5 * tf.add_n([tf.reduce_sum(tf.square(momentum) / m, axis)
                              for momentum, m in zip(p, mass)])
    # (n_particles, n)
#return potential + kinetic
    return kinetic


def leapfrog_integrator(q, p, step_size1, step_size2, grad, mass):
    # [(n_particles, n, n_z)]
    q = [x + step_size1 * y for x, y in zip(q, velocity(p, mass))]
    # p = p + epsilon / 2 * gradient q
    # [(n_particles, n, n_z)]
    grads = grad(q)
    # [(n_particles, n, n_z)]
    p = [x + step_size2 * y for x, y in zip(p, grads)]
    return q, p


def get_acceptance_rate(q, p, new_q, new_p, log_posterior, mass, axis):
    # (n_particles, n)
    old_hamiltonian = hamiltonian(q, p, log_posterior, mass, axis)
    # (n_particles, n)
    new_hamiltonian = hamiltonian(new_q, new_p, log_posterior, mass, axis)
    # (n_particles, n)
    return old_hamiltonian, new_hamiltonian, \
            tf.exp(tf.minimum(-new_hamiltonian + old_hamiltonian, 0.0))


class StepsizeTuner:
    def __init__(self, m_adapt=50, gamma=0.05, t0=10, kappa=0.75, delta=0.8):
        with tf.name_scope("StepsizeTuner"):
            self.m_adapt = tf.convert_to_tensor(m_adapt, name="m_adapt")
            self.gamma = tf.convert_to_tensor(gamma, name="gamma")
            self.t0 = tf.convert_to_tensor(t0, name="t0")
            self.kappa = tf.convert_to_tensor(kappa, name="kappa")
            self.delta = tf.convert_to_tensor(delta)

            self.step = tf.Variable(0, name="step")
            self.total_step = tf.Variable(0, name="total_step")
            self.log_epsilon_bar = tf.Variable(0.0, name="log_epsilon_bar")
            self.h_bar = tf.Variable(0.0, name="h_bar")
            self.mu = tf.Variable(0.0, name="mu")

    @add_name_scope
    def restart(self, stepsize):
        update_mu = tf.assign(self.mu, tf.log(10 * stepsize))
        update_step = tf.assign(self.step, 0)
        update_log_epsilon_bar = tf.assign(self.log_epsilon_bar, 0.0)
        update_h_bar = tf.assign(self.h_bar, 0.0)
        return update_mu, update_step, update_log_epsilon_bar, update_h_bar

    @add_name_scope
    def tune(self, acceptance_rate):
        new_step = tf.assign(self.step, self.step + 1)
        new_total_step = tf.assign(self.total_step, self.total_step + 1)

        def adapt_stepsize():
            step = tf.to_float(new_step)
            rate1 = tf.div(1.0, step + tf.to_float(self.t0))
            new_h_bar = tf.assign(self.h_bar, (1 - rate1) * self.h_bar +
                                  rate1 * (self.delta - acceptance_rate))
            log_epsilon = self.mu - tf.sqrt(step) / \
                self.gamma * new_h_bar
            rate = tf.pow(step, -self.kappa)
            new_log_epsilon_bar = tf.assign(
                self.log_epsilon_bar,
                rate * log_epsilon + (1 - rate) * self.log_epsilon_bar)
            with tf.control_dependencies([new_log_epsilon_bar]):
                new_log_epsilon = tf.identity(log_epsilon)

            return tf.exp(new_log_epsilon)

        c = tf.cond(new_total_step < self.m_adapt,
                    adapt_stepsize,
                    lambda: tf.exp(self.log_epsilon_bar))

        return c

    @add_name_scope
    def restart_and_tune(self, stepsize, acceptance_rate):
        with tf.control_dependencies(self.restart(stepsize)):
            step_size = self.tune(acceptance_rate)
        return step_size


class VarianceEstimator:
    def __init__(self, shape):
        self.shape = shape
        self.num_vars = len(shape)
        with tf.name_scope("VarianceEstimator"):
            self.count = tf.Variable(0.0, name="count")
            self.mean = [tf.Variable(tf.zeros(s), name="mean") for s in shape]
            self.s = [tf.Variable(tf.zeros(s), name="s") for s in shape]

    @add_name_scope
    def reset(self):
        update_count = tf.assign(self.count, 0.0)
        update_mean = [tf.assign(x, tf.zeros(s))
                       for x, s in zip(self.mean, self.shape)]
        update_s = [tf.assign(x, tf.zeros(s))
                    for x, s in zip(self.s, self.shape)]
        return tf.tuple([update_count] + update_mean + update_s)

    @add_name_scope
    def add(self, x):
        new_count = tf.assign(self.count, self.count + 1)
        new_mean = []
        new_s = []
        for i in range(self.num_vars):
            delta = x[i] - self.mean[i]
            new_mean.append(
                tf.assign(self.mean[i], self.mean[i] + delta / new_count))
            new_s.append(
                tf.assign(self.s[i], self.s[i] + delta * (x[i] - new_mean[i])))
        return tf.tuple([new_count] + new_mean + new_s)

    @add_name_scope
    def variance(self):
        return map(lambda x: x / (self.count - 1), self.s)

    @add_name_scope
    def precision(self):
        rate = self.count / (self.count + 5)
        res = [1 / (rate * x + (1 - rate) * 1e-3) for x in self.variance()]
        return res


class HMC:
    def __init__(self, step_size=1, n_leapfrogs=10, target_acceptance_rate=0.8,
                 m_adapt=50, gamma=0.05, t0=10, kappa=0.75):
        with tf.name_scope("HMC"):
            self.step_size = tf.Variable(step_size, name="step_size")
            self.n_leapfrogs = tf.convert_to_tensor(n_leapfrogs,
                                                    name="n_leapfrogs")
            self.target_acceptance_rate = tf.convert_to_tensor(
                target_acceptance_rate, name="target_acceptance_rate")
            self.t = tf.Variable(0, name="t")
            # self.step_size_tuner = StepsizeTuner(
            #     m_adapt=m_adapt, gamma=gamma, t0=t0, kappa=kappa,
            #     delta=target_acceptance_rate)
            #
            # self.m_adapt = tf.convert_to_tensor(m_adapt, name="m_adapt")
            # self.init_buffer = tf.convert_to_tensor(int(m_adapt * 0.2),
            #                                         name="init_buffer")
            # self.term_buffer = tf.convert_to_tensor(int(m_adapt * 0.5),
            #                                         name="term_buffer")
            # self.base_window = m_adapt - self.init_buffer - self.term_buffer
            # self.next_window = self.init_buffer + self.base_window

    @add_name_scope
    def sample(self, log_posterior, observed, latent, axis,
               mass=None, given=None):
        # latent_v: [(n_particles, n, n_z)]
        latent_k, latent_v = [list(i) for i in zip(*six.iteritems(latent))]
        # [(n_particles, n, n_z)]
        self.q = copy(latent_v)
        self.shapes = [x.get_shape() for x in self.q]
        # variance_estimator = VarianceEstimator(self.shapes)
        #
        # # Estimate covariance
        # add_op = tf.cond(
        #     tf.logical_and(self.t >= self.init_buffer,
        #                    self.t < self.m_adapt - self.term_buffer),
        #     lambda: variance_estimator.add(self.q),
        #     lambda: [tf.constant(1.0)] + self.q + self.q)
        #
        # def update_mass():
        #     new_mass = [tf.assign(x, y) for x, y in
        #                 zip(mass, variance_estimator.precision())]
        #     with tf.control_dependencies(new_mass):
        #         variance_estimator.reset()
        #     return new_mass
        #
        # with tf.control_dependencies(add_op):
        #     current_mass = tf.cond(tf.equal(self.t, self.next_window),
        #                            update_mass,
        #                            lambda: mass)
        #     if len(self.shapes) == 1:
        #         current_mass = [current_mass]

        # [(n_particles, n, n_z)]
        current_mass = mass

        # [(n_particles, n, n_z)]
        p = random_momentum(current_mass)

        def get_gradient(var_list):
            # (n_particles, n)
            log_p = log_posterior(dict(zip(latent_k, var_list)),
                                  observed, given)
            # (n_particles, n, n_z)
            latent_grads = tf.gradients(log_p, var_list)
            return log_p, latent_grads

        # # Initialize step size
        # def init_step_size():
        #     factor = 1.1
        #
        #     def loop_cond(step_size, last_acceptance_rate, cond):
        #         return cond
        #
        #     def loop_body(step_size, last_acceptance_rate, cond):
        #         # Calculate acceptance_rate
        #         new_q, new_p = leapfrog_integrator(
        #             self.q, p, tf.constant(0.0), step_size / 2,
        #             lambda var_list: get_gradient(var_list)[1], current_mass)
        #         new_q, new_p = leapfrog_integrator(
        #             new_q, new_p, step_size, step_size / 2,
        #             lambda var_list: get_gradient(var_list)[1], current_mass)
        #         _, _, acceptance_rate = get_acceptance_rate(
        #             self.q, p, new_q, new_p, log_posterior, current_mass)
        #
        #         # Change step size and stopping criteria
        #         new_step_size = tf.cond(
        #             tf.less(acceptance_rate, self.target_acceptance_rate),
        #             lambda: step_size * (1.0 / factor),
        #             lambda: step_size * factor)
        #
        #         cond = tf.logical_not(tf.logical_xor(
        #             tf.less(last_acceptance_rate, self.target_acceptance_rate),
        #             tf.less(acceptance_rate, self.target_acceptance_rate)))
        #
        #         # return [tf.Print(new_step_size,
        #         #                  [new_step_size, acceptance_rate]),
        #         #         acceptance_rate, cond]
        #         return [new_step_size, acceptance_rate, cond]
        #
        #     init_step_size_, new_acceptance_rate, _ = tf.while_loop(
        #         loop_cond,
        #         loop_body,
        #         [self.step_size, tf.constant(1.0), tf.constant(True)]
        #     )
        #     # init_step_size_ = tf.Print(init_step_size_, [init_step_size_, new_acceptance_rate])
        #     return tf.assign(self.step_size, init_step_size_)
        #
        # new_step_size = tf.cond(
        #     tf.logical_or(tf.equal(self.t, 0),
        #                   tf.equal(self.t, self.next_window)),
        #     init_step_size,
        #     lambda: self.step_size)
        new_step_size = self.step_size

        # Leapfrog
        # [(n_particles, n, n_z)]
        current_p = p
        # [(n_particles, n, n_z)]
        current_q = copy(self.q)

        def loop_cond(i, current_q, current_p):
            return i < self.n_leapfrogs + 1

        def loop_body(i, current_q, current_p):
            step_size1 = tf.cond(i > 0,
                                 lambda: new_step_size,
                                 lambda: tf.constant(0.0, dtype=tf.float32))

            step_size2 = tf.cond(tf.logical_and(tf.less(i, self.n_leapfrogs),
                                                tf.less(0, i)),
                                 lambda: new_step_size,
                                 lambda: new_step_size / 2)

            # [(n_particles, n, n_z)], [(n_particles, n, n_z)]
            current_q, current_p = leapfrog_integrator(
                current_q,
                current_p,
                step_size1,
                step_size2,
                lambda q: get_gradient(q)[1],
                current_mass
            )
            return [i + 1, current_q, current_p]

        i = tf.constant(0)
        # [(n_particles, n, n_z)], [(n_particles, n, n_z)]
        _, current_q, current_p = tf.while_loop(loop_cond,
                                                loop_body,
                                                [i, current_q, current_p],
                                                back_prop=False,
                                                parallel_iterations=1)

        # Hamiltonian
        log_posterior_ = lambda q: log_posterior(dict(zip(latent_k, q)),
                                                 observed, given)
        # (n_particles, n)
        old_hamiltonian, new_hamiltonian, acceptance_rate = \
            get_acceptance_rate(self.q, p, current_q, current_p,
                                log_posterior_, current_mass, axis)
        # (n_particles, n)
        u01 = tf.random_uniform(shape=tf.shape(acceptance_rate))

        # (n_particles, n)
        print(u01.get_shape().as_list())
        print(current_q[0].get_shape().as_list())
        print(self.q[0].get_shape().as_list())
        def accept(k, v):
            tile_arr = [1] * len(v.get_shape())
            tile_arr[-1] = tf.shape(v)[-1]
            accept = tf.tile(tf.expand_dims(u01 < acceptance_rate, -1),
                             tile_arr)
            return tf.select(accept, k, v)
        new_q = [accept(k, v) for k, v in zip(current_q, self.q)]

        # # This is because tf.cond returns a single Tensor if the branch
        # # function returns a list of a single Tensor.
        # if len(self.q) == 1:
        #     new_q = [new_q]

        # Tune step size
        # with tf.control_dependencies([acceptance_rate]):
        #     new_stepsize = tf.cond(
        #         tf.logical_or(tf.equal(self.t, 0),
        #                       tf.equal(self.t, self.next_window)),
        #         lambda: self.step_size_tuner.restart_and_tune(new_step_size,
        #                                                       acceptance_rate),
        #         lambda: self.step_size_tuner.tune(acceptance_rate))
        #     update_stepsize = tf.assign(self.step_size, new_stepsize)
        #
        # with tf.control_dependencies([update_stepsize]):
        update_t = tf.assign(self.t, self.t + 1)

        return 0
        return new_q, p, old_hamiltonian, new_hamiltonian, acceptance_rate, \
            update_t#, update_stepsize
