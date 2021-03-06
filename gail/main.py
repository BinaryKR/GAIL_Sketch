import os
import gym
import pickle
import argparse
import numpy as np
from collections import deque

from environment import Env


import torch
import torch.optim as optim
from tensorboardX import SummaryWriter 

from utils.utils import *
from utils.zfilter import ZFilter
from model import Actor, Critic, Discriminator
from train_model import train_actor_critic, train_discrim

import matplotlib.pyplot as plt
temp_learner = []
temp_expert = []

parser = argparse.ArgumentParser(description='PyTorch GAIL')
parser.add_argument('--env_name', type=str, default="Hopper-v2", 
                    help='name of the environment to run')
parser.add_argument('--load_model', type=str, default=None,
                    help='path to load the saved model')
parser.add_argument('--render', action="store_true", default=False, 
                    help='if you dont want to render, set this to False')
parser.add_argument('--gamma', type=float, default=0.99, 
                    help='discounted factor (default: 0.99)')
parser.add_argument('--lamda', type=float, default=0.98, 
                    help='GAE hyper-parameter (default: 0.98)')
parser.add_argument('--hidden_size', type=int, default=100,
                    help='hidden unit size of actor, critic and discrim networks (default: 100)')
parser.add_argument('--learning_rate', type=float, default=3e-4, 
                    help='learning rate of models (default: 3e-4)')
parser.add_argument('--l2_rate', type=float, default=1e-3, 
                    help='l2 regularizer coefficient (default: 1e-3)')
parser.add_argument('--clip_param', type=float, default=0.2, 
                    help='clipping parameter for PPO (default: 0.2)')
parser.add_argument('--discrim_update_num', type=int, default=2, 
                    help='update number of discriminator (default: 2)')
parser.add_argument('--actor_critic_update_num', type=int, default=10, 
                    help='update number of actor-critic (default: 10)')
parser.add_argument('--total_sample_size', type=int, default=2048, 
                    help='total sample size to collect before PPO update (default: 2048)')
parser.add_argument('--batch_size', type=int, default=64, 
                    help='batch size to update (default: 64)')
parser.add_argument('--suspend_accu_exp', type=float, default=0.8,
                    help='accuracy for suspending discriminator about expert data (default: 0.8)')
parser.add_argument('--suspend_accu_gen', type=float, default=0.8,
                    help='accuracy for suspending discriminator about generated data (default: 0.8)')

parser.add_argument('--max_iter_num', type=int, default=25000,
                    help='maximal number of main iterations (default: 4000)')
# parser.add_argument('--max_iter_num', type=int, default=4000,
                    # help='maximal number of main iterations (default: 4000)')


parser.add_argument('--seed', type=int, default=500,
                    help='random seed (default: 500)')
parser.add_argument('--logdir', type=str, default='logs',
                    help='tensorboardx logs directory')
args = parser.parse_args()

# env.build_canvas ####f
def main():
    expert_demo= pickle.load(open('./Ree1_expert.p', "rb"))
    # Ree1 : action 1
    # Ree2 : action 100
    # Ree3 : action 50
    # Ree4 : action 10
    # Ree5 : action 4
    # Ree6 : action 0.5


    # print('expert_demo_shape : ', np.array(expert_demo).shape)
    expert_x = int(expert_demo[1][0])
    expert_y = int(expert_demo[1][1])
    env = Env(expert_x, expert_y)
    # env = Env(0,0)

    # env.seed(args.seed)
    torch.manual_seed(args.seed)

    num_inputs = 2
    num_actions = 8
    running_state = ZFilter((num_inputs,), clip=5)

    print('state size:', num_inputs) 
    print('action size:', num_actions)

    actor = Actor(num_inputs, num_actions, args)
    critic = Critic(num_inputs, args)
    discrim = Discriminator(num_inputs + num_actions, args)

    actor_optim = optim.Adam(actor.parameters(), lr=args.learning_rate)
    critic_optim = optim.Adam(critic.parameters(), lr=args.learning_rate, 
                              weight_decay=args.l2_rate) 
    discrim_optim = optim.Adam(discrim.parameters(), lr=args.learning_rate)
    
    # load demonstrations
    # expert_demo, _ = pickle.load(open('./expert_demo/expert_demo.p', "rb"))

    demonstrations = np.array(expert_demo[0])

    # print("demonstrations.shape", demonstrations.shape)

    
    writer = SummaryWriter(args.logdir)

    if args.load_model is not None:
        saved_ckpt_path = os.path.join(os.getcwd(), 'save_model', str(args.load_model))
        ckpt = torch.load(saved_ckpt_path)

        actor.load_state_dict(ckpt['actor'])
        critic.load_state_dict(ckpt['critic'])
        discrim.load_state_dict(ckpt['discrim'])

        running_state.rs.n = ckpt['z_filter_n']
        running_state.rs.mean = ckpt['z_filter_m']
        running_state.rs.sum_square = ckpt['z_filter_s']

        print("Loaded OK ex. Zfilter N {}".format(running_state.rs.n))


    episodes = 0
    train_discrim_flag = True

    for iter in range(args.max_iter_num):
        actor.eval(), critic.eval()
        memory = deque()

        steps = 0
        scores = []

        while steps < args.total_sample_size: 
            state = env.reset()
            score = 0

            state = running_state(state)
            
            for _ in range(1000):
                if args.render:
                    env.render()

                steps += 1

                mu, std = actor(torch.Tensor(state).unsqueeze(0))
                action2 = np.argmax(get_action(mu, std)[0])
                action = get_action(mu, std)[0]
                next_state, reward, done, _ = env.step(action2)
                # next_state, reward, done, _ = env.step(action)
                irl_reward = get_reward(discrim, state, action)

                if done:
                    mask = 0
                else:
                    mask = 1

                memory.append([state, action, irl_reward, mask])

                next_state = running_state(next_state)
                state = next_state

                score += reward

                if done:
                    break
            
            episodes += 1
            scores.append(score)
        
        score_avg = np.mean(scores)
        print('{}:: {} episode score is {:.2f}'.format(iter, episodes, score_avg))
        writer.add_scalar('log/score', float(score_avg), iter)

        actor.train(), critic.train(), discrim.train()
        if train_discrim_flag:
            expert_acc, learner_acc = train_discrim(discrim, memory, discrim_optim, demonstrations, args)
            print("Expert: %.2f%% | Learner: %.2f%%" % (expert_acc * 100, learner_acc * 100))

            temp_learner.append(learner_acc * 100)
            temp_expert.append(expert_acc * 100)

            if ((expert_acc > args.suspend_accu_exp and learner_acc > args.suspend_accu_gen and iter % 55==0)  or iter % 50 == 0):
                # train_discrim_flag = False
                plt.plot(temp_learner, label = 'learner')
                plt.plot(temp_expert, label = 'expert')
                plt.xlabel('Episode')
                plt.ylabel('Accuracy')
                plt.xticks([])
                plt.legend()
                plt.savefig('accuracy{}.png'.format(iter))
                # plt.show()

                model_path = 'C:/Users/USER/9 GAIL/lets-do-irl/mujoco/gail'
                ckpt_path = os.path.join(model_path, 'ckpt_' + str(score_avg) + '.pth.tar')

                print("check path",ckpt_path)
                save_checkpoint({
                    'actor': actor.state_dict(),
                    'critic': critic.state_dict(),
                    'discrim': discrim.state_dict(),
                    'z_filter_n': running_state.rs.n,
                    'z_filter_m': running_state.rs.mean,
                    'z_filter_s': running_state.rs.sum_square,
                    'args': args,
                    'score': score_avg
                }, filename=ckpt_path)


        train_actor_critic(actor, critic, memory, actor_optim, critic_optim, args)

        if iter % 100:
            score_avg = int(score_avg)

            model_path = os.path.join(os.getcwd(),'save_model')
            if not os.path.isdir(model_path):
                os.makedirs(model_path)

            model_path = 'C:/Users/USER/9 GAIL/lets-do-irl/mujoco/gail'
            ckpt_path = os.path.join(model_path, 'ckpt_'+ str(score_avg)+'.pth.tar')

            save_checkpoint({
                'actor': actor.state_dict(),
                'critic': critic.state_dict(),
                'discrim': discrim.state_dict(),
                'z_filter_n':running_state.rs.n,
                'z_filter_m': running_state.rs.mean,
                'z_filter_s': running_state.rs.sum_square,
                'args': args,
                'score': score_avg
            }, filename=ckpt_path)
    plt.plot(temp_learner)
    plt.plot(temp_expert)
    plt.xlabel('Episode')
    plt.ylabel('Accuracy')
    plt.xticks([])
    plt.savefig('accuracy.png')
    # plt.show()
if __name__=="__main__":
    main()