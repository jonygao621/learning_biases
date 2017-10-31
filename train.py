# Code taken from https://github.com/TheAbhiKumar/tensorflow-value-iteration-networks

import time
import numpy as np
import random
import tensorflow as tf
import matplotlib
matplotlib.use("tkagg")
import matplotlib.pyplot as plt


import agents
from gridworld_data import generate_gridworld_irl
from model import VI_Block
from utils import fmt_row
# from tf.saved_model.tag_constants import SERVING, TRAINING

import sys
sys.path.insert(0, '../tensorflow-value-iteration-networks')

# Data
tf.app.flags.DEFINE_boolean(
    'simple_mdp', False, 'Whether to use the simple random MDP generator')
tf.app.flags.DEFINE_integer('imsize', 8, 'Size of input image')
tf.app.flags.DEFINE_float(
    'wall_prob', 0.05,
    'Probability of having a wall at any particular space in the gridworld. '
    'Has no effect if --simple_mdp is False.')
tf.app.flags.DEFINE_float(
    'reward_prob', 0.05,
    'Probability of having a reward at any particular space in the gridworld')
tf.app.flags.DEFINE_float(
    'action_distance_threshold', 0.5,
    'Minimum distance between two action distributions to be "different"')
tf.app.flags.DEFINE_integer(
    'num_train', 500, 'Number of examples for training the planning module')
tf.app.flags.DEFINE_integer(
    'num_test', 200, 'Number of examples for testing the planning module')

# Hyperparameters
tf.app.flags.DEFINE_float(
    'vin_regularizer_C', 0.0001, 'Regularization constant for the VIN')
tf.app.flags.DEFINE_float(
    'reward_regularizer_C', 0.0001, 'Regularization constant for the reward')
tf.app.flags.DEFINE_float(
    'lr', 0.01, 'Learning rate when training the planning module')
tf.app.flags.DEFINE_float(
    'reward_lr', 0.1, 'Learning rate when inferring a reward function')
tf.app.flags.DEFINE_integer(
    'epochs', 50, 'Number of epochs to train the planning module for')
tf.app.flags.DEFINE_integer(
    'reward_epochs', 50, 'Number of epochs when inferring a reward function')
tf.app.flags.DEFINE_integer('k', 10, 'Number of value iterations')
tf.app.flags.DEFINE_integer('ch_h', 150, 'Channels in initial hidden layer')
tf.app.flags.DEFINE_integer('ch_q', 5, 'Channels in q layer')
tf.app.flags.DEFINE_integer('num_actions', 5, 'Number of actions')
tf.app.flags.DEFINE_integer('batchsize', 12, 'Batch size')
tf.app.flags.DEFINE_integer(
    'statebatchsize', 10,
    'Number of state inputs for each sample (real number, technically is k+1)')

# Agent
tf.app.flags.DEFINE_string(
    'agent', 'optimal', 'Agent to generate training data with')
tf.app.flags.DEFINE_float('gamma', 1.0, 'Discount factor')
tf.app.flags.DEFINE_float('beta', None, 'Noise when selecting actions')
tf.app.flags.DEFINE_integer(
    'num_iters', 50,
    'Number of iterations of value iteration the agent should run.')
tf.app.flags.DEFINE_integer(
    'max_delay', 5,
    'Maximum delay that the agent should use. '
    'Only affects naive/sophisticated and myopic agents.')
tf.app.flags.DEFINE_float(
    'hyperbolic_constant', 1.0,
    'Discount for the future for hyperbolic time discounters')

# Other Agent
tf.app.flags.DEFINE_string(
    'other_agent', None,
    'Agent to distinguish from. '
    'In particular, when generating training data, we print the number of '
    'training examples on which agent and other_agent would choose different '
    'action distributions.')
tf.app.flags.DEFINE_float('other_gamma', 1.0, 'Gamma for other agent')
tf.app.flags.DEFINE_float('other_beta', None, 'Beta for other agent')
tf.app.flags.DEFINE_integer('other_num_iters', 50, 'Num iters for other agent')
tf.app.flags.DEFINE_integer('other_max_delay', 5, 'Max delay for other agent')
tf.app.flags.DEFINE_float(
    'other_hyperbolic_constant', 1.0, 'Hyperbolic constant for other agent')

# Miscellaneous
tf.app.flags.DEFINE_integer('seed', 0, 'Random seed for both numpy and random')
tf.app.flags.DEFINE_integer(
    'display_step', 1, 'Print summary output every n epochs')
tf.app.flags.DEFINE_boolean('log', False, 'Enables tensorboard summary')
tf.app.flags.DEFINE_string(
    'logdir', '/tmp/planner-vin/', 'Directory to store tensorboard summary')

config = tf.app.flags.FLAGS

# It is required that the number of unknown reward functions be equal to the
# batch size. If we tried to train multiple batches, then they would all be
# modifying the same reward function, which would be bad.
config.num_mdps = config.batchsize

np.random.seed(config.seed)
random.seed(config.seed)

# Tensorflow refuses to have a Variable whose shape is not fully determined. As
# a result, we must set the batch size to a constant which cannot be changed
# during a particular run. (We need to use a Variable for the reward so that the
# reward can be trained in step 2.)
batch_size, state_batch_size = config.batchsize, config.statebatchsize
imsize = config.imsize
num_actions = config.num_actions

image = tf.placeholder(
    tf.float32, name="image", shape=[batch_size, imsize, imsize])
reward = tf.Variable(
    tf.zeros([batch_size, imsize, imsize]), name='reward', trainable=False)
X  = tf.stack([image, reward], axis=-1)
# symbolic input batches of vertical positions
S1 = tf.placeholder(tf.int32, name="S1", shape=[batch_size, state_batch_size])
# symbolic input batches of horizontal positions
S2 = tf.placeholder(tf.int32, name="S2", shape=[batch_size, state_batch_size])
y  = tf.placeholder(tf.float32, name="y",  shape=[batch_size * state_batch_size, num_actions])

# Construct model (Value Iteration Network)
logits, nn = VI_Block(X, S1, S2, config)

# Define losses
cross_entropy = tf.nn.softmax_cross_entropy_with_logits(
    logits=logits, labels=y, name='cross_entropy')
cross_entropy_mean = tf.reduce_mean(cross_entropy, name='cross_entropy_mean')
tf.add_to_collection('losses', cross_entropy_mean)

logits_cost = tf.add_n(tf.get_collection('losses'), name='logits_loss')
if config.vin_regularizer_C > 0:
    vin_regularizer_cost = tf.add_n(
        tf.get_collection(tf.GraphKeys.REGULARIZATION_LOSSES), name='vin_loss')
    step1_cost = logits_cost + vin_regularizer_cost
else:
    step1_cost = logits_cost

if config.reward_regularizer_C > 0:
    l1_regularizer = tf.contrib.layers.l1_regularizer(config.reward_regularizer_C)
    reward_regularizer_cost = tf.contrib.layers.apply_regularization(
        l1_regularizer, [reward])
    step2_cost = logits_cost + reward_regularizer_cost
else:
    step2_cost = logits_cost

# Define optimizers
planner_optimizer = tf.train.RMSPropOptimizer(
    learning_rate=config.lr, epsilon=1e-6, centered=True)
planner_optimize_op = planner_optimizer.minimize(step1_cost)
reward_optimizer = tf.train.RMSPropOptimizer(
    learning_rate=config.reward_lr, epsilon=1e-6, centered=True)
reward_optimize_op = reward_optimizer.minimize(step2_cost, var_list=[reward])

# Test model & calculate accuracy
cp = tf.cast(tf.argmax(nn, 1), tf.int32)
# Use the most probable action even for the gold labels
most_likely_y = tf.cast(tf.argmax(y, axis=1), tf.int32)
err = tf.reduce_mean(tf.cast(tf.not_equal(cp, most_likely_y), dtype=tf.float32))

# Initializing the variables
init = tf.global_variables_initializer()
saver = tf.train.Saver()

# Saving model in SavedModel format
builder = tf.saved_model.builder.SavedModelBuilder(config.logdir+'model/')

imagetrain, rewardtrain, S1train, S2train, ytrain, \
imagetest1, rewardtest1, S1test1, S2test1, ytest1, \
imagetest2, rewardtest2, S1test2, S2test2, ytest2 = generate_gridworld_irl(config)
ytrain = np.reshape(ytrain, [-1, num_actions])
ytest1 = np.reshape(ytest1, [-1, num_actions])
ytest2 = np.reshape(ytest2, [-1, num_actions])

# Launch the graph
with tf.Session() as sess:
    if config.log:
        for var in tf.trainable_variables():
            tf.summary.histogram(var.op.name, var)
        summary_op = tf.summary.merge_all()
        summary_writer = tf.summary.FileWriter(config.logdir, sess.graph)
    sess.run(init)
    builder.add_meta_graph_and_variables(sess, [tf.saved_model.tag_constants.SERVING])

    def run_epoch(data, ops_to_run, ops_to_average):
        tstart = time.time()
        image_data, reward_data, S1_data, S2_data, y_data = data
        averages = [0.0] * len(ops_to_average)
        num_batches = int(image_data.shape[0] / batch_size)
        # Loop over all batches
        for i in range(num_batches):
            start, end = i * batch_size, (i + 1) * batch_size
            fd = {
                image: image_data[start:end],
                reward: reward_data[start:end],
                S1: S1_data[start:end],
                S2: S2_data[start:end],
                y: y_data[start * state_batch_size:end * state_batch_size]
            }
            results = sess.run(ops_to_run + ops_to_average, feed_dict=fd)
            num_ops_to_run = len(ops_to_run)
            op_results, average_op_results = results[:num_ops_to_run], results[num_ops_to_run:]
            averages = [x + y for x, y in zip(averages, average_op_results)]
        
        averages = [x / num_batches for x in averages]
        elapsed = time.time() - tstart
        return op_results, averages, elapsed

    train_data = (imagetrain, rewardtrain, S1train, S2train, ytrain)
    test1_data = (imagetest1, rewardtest1, S1test1, S2test1, ytest1)

    print(fmt_row(10, ["Epoch", "Train Cost", "Train Err", "Valid Err", "Epoch Time"]))
    try:
        for epoch in range(int(config.epochs)):
            _, (avg_cost, avg_err), elapsed = run_epoch(
                train_data, [planner_optimize_op], [step1_cost, err])
            # Display logs per epoch step
            if epoch % config.display_step == 0:
                _, (test1_err,), _ = run_epoch(test1_data, [], [err])
                print(fmt_row(10, [epoch, avg_cost, avg_err, test1_err, elapsed]))
            if config.log:
                summary = tf.Summary()
                summary.ParseFromString(sess.run(summary_op))
                summary.value.add(tag='Average error', simple_value=float(avg_err))
                summary.value.add(tag='Average cost', simple_value=float(avg_cost))
                summary_writer.add_summary(summary, epoch)
                # saver.save(sess, config.logdir)
    except KeyboardInterrupt:
        pass
  
    print("Finished training!")
    _, (test1_err,), _ = run_epoch(test1_data, [], [err])
    # saving SavedModel instance
    savepath = builder.save()
    print("model saved 2: {}".format(savepath))
    print('Final Accuracy: ' + str(100 * (1 - test1_err)))

    print('Beginning IRL inference')
    print(fmt_row(10, ["Iteration", "Train Cost", "Train Err", "Iter Time"]))
    try:
        for epoch in range(config.reward_epochs):
            tstart = time.time()
            fd = {
                image: imagetest2,
                S1: S1test2,
                S2: S2test2,
                y: ytest2,
            }
            _, predicted_reward, e_, c_ = sess.run(
                [reward_optimize_op, reward, err, step2_cost], feed_dict=fd)
            elapsed = time.time() - tstart
            print(fmt_row(10, [epoch, c_, e_, elapsed]))
    except KeyboardInterrupt:
        pass

    print('The first set of walls is:')
    print(imagetest2[0])
    print('The first reward should be:')
    print(rewardtest2[0])
    inferred_reward = reward.eval()[0]
    print('The first reward should be:')
    print(rewardtest2[0])
    normalized_inferred_reward = inferred_reward / inferred_reward.max()
    print('The inferred reward is:')
    print(normalized_inferred_reward)

    plot_reward(reward_test2[0], normalized_inferred_reward)

def plot_reward(label, inferred_reward, filename='reward_comparison.png'):
    """Plots rewards (true and predicted) and saves them to a file.

    Inferred_reward should be normalized before.
    """
    # set up plot
    fig, axes = plt.subplots(1,2)

    # truth plot
    true = axes[0].imshow(label,cmap='hot',interpolation='nearest')
    axes[0].set_title("Truth")
    cbaxes = fig.add_axes([0.02, 0.1, 0.02, 0.8])
    cb = plt.colorbar(true, cax=cbaxes)

    # inferred plot
    tensor = axes[1].imshow(normalized_inferred_reward, cmap='hot', interpolation='nearest')
    axes[1].set_title("Predicted")
    cbaxes2 = fig.add_axes([0.925, 0.1, 0.02, 0.8])
    plt.colorbar(tensor, cax=cbaxes2)

    # titleing
    fig.suptitle("Comparison of Reward Functions")
    # saving to file
    fig.savefig(filename)

